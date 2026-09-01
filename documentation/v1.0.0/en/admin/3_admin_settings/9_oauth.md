# OAuth

**Admin Settings > OAuth** configures Google, GitHub, Slack, Microsoft, and Apple sign-in. Google, GitHub, and Slack can also support separate managed workspace connections.

## Common Setup

1. Configure the primary **Public URLs** origin under [General](3_general.md) and restart Omlorix.
2. Create the application at the provider and register the callback shown for that integration.
3. Enter credentials only in the protected fields.
4. Enable the provider configuration and, where separate, its login control.
5. Set the domain, organization, workspace, or tenant restrictions and decide whether that provider may create accounts.
6. Test both an existing linked account and a new permitted account.

Global **Enable sign-up** under [Login](6_login.md) must also be on before OAuth can create a user. New accounts receive only the configured ordinary or pending role. A matching email does not silently link an existing account; the user must complete the authorized linking workflow.

## Provider Controls

| Provider | Main UI settings |
| --- | --- |
| **Google OAuth** | configuration and login toggles, client credentials, profile-picture import, button text, allowed domains, signup, **Google Picker API Key**, and **Google Picker App ID** |
| **GitHub OAuth** | configuration and login toggles, **GitHub Base URL**, client credentials, profile-picture import, allowed domains and organizations, signup, and **GitHub Connection Scope Tier** |
| **Slack OAuth** | configuration and login toggles, client credentials, profile-picture import, allowed email domains and workspace IDs, signup, and **Slack Connection Scope Tier** |
| **Microsoft OAuth** | configuration and login toggles, **Microsoft Account Tenant**, client credentials, profile-picture import, allowed domains and tenant IDs, and signup |
| **Apple Sign In** | login toggle, **Apple Service ID**, **Apple Team ID**, **Apple Key ID**, **Apple Private Key**, allowed domains, signup, and button text |

Managed GitHub workspace connections require GitHub.com; a self-hosted **GitHub Base URL** is supported for sign-in only. Google Picker credentials should use the same appropriately restricted Google Cloud project as the Google OAuth application.

## Login and Workspace Permissions Are Separate

Social sign-in requests identity information. Managed workspace connections request separate permissions for user tools:

- **GitHub Connection Scope Tier:** **Profile only**, **Repository access**, or **Extended access**
- **Slack Connection Scope Tier:** **Public channels only**, **Workspace read access**, or **Workspace write access**

Changing a connection tier does not change sign-in permissions. Users must reconnect to grant the new workspace permissions; revoke old grants at the provider when immediate removal matters.

GitHub's **Repository access** tier uses the provider's broad repository permission because GitHub OAuth apps do not offer a private-repository read-only scope. Approve it only when that access is required.

Register every callback requested by Omlorix. Do not add workspace permissions to the identity-only sign-in flow.

## Security and Troubleshooting

- choose the narrowest domains, organizations, tenants, workspaces, and connection tier
- disclose profile-picture import because Omlorix stores a copy
- account for Offline Mode and outbound policy
- rotate an exposed credential at the provider, update the protected field, and revoke affected grants
- remember that disabling new authorization does not revoke tokens already issued by the provider

If a button is missing, check global sign-in, both provider toggles, required credentials, and outbound access. If a new account is rejected, check global signup, provider signup, restrictions, legal acceptance, and default-role policy. If sign-in works but workspace tools fail, check the separate connection tier and have the user reconnect.
