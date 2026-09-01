# First Steps

A new Omlorix instance has two browser steps: create the first account, then complete **Server Setup**.

## Create the Owner

The first account on an empty instance becomes the single protected **Owner**, regardless of the default role for later accounts. The Owner has every Admin capability and is the only role that can grant or remove Admin authority.

Protect it immediately:

- use a unique password and correct email address
- add a second usable sign-in factor when available
- keep recovery material in your organization's credential vault
- create a separate Admin account for routine work

Do not use a shared mailbox or temporary person's account as Owner. Confirm that the selected sign-in and recovery path will still work during an identity-provider outage.

The Owner role cannot later be assigned through ordinary user creation, import, OAuth, LDAP, SSO, or SCIM. See [Roles and Instance Ownership](../4_user_group_management/2_roles_and_ownership.md).

## Complete Server Setup

The guided screens configure:

1. **Application Name**
2. **Public URLs**
3. light and dark logos plus the app icon
4. **Default user role**

### Public URLs

Enter every stable browser origin used for Omlorix, such as `https://chat.example.com`. Do not include a path, query, fragment, or credentials. The first entry is primary for generated links; all entries are accepted for sensitive browser authentication flows.

Replace temporary localhost or IP values before enabling passkeys, password reset, OAuth, SSO, or shared links. Changing this list later requires an application restart.

### Branding

Upload both logo variants so they remain readable in light and dark themes. Use a simple square image for the app icon. SVG, PNG, JPEG, and WebP are accepted; the screen shows the current size limits.

### Default User Role

- **Pending:** an administrator must approve the account before normal use.
- **User:** the account receives normal user access immediately.

Automatic signup and provisioning never grant Admin or Owner. LDAP and enterprise SSO can have their own User/Pending defaults.

## What to Configure Next

Server Setup does not configure models, email, identity providers, groups, or legal content. Before inviting users:

1. Check [General](../3_admin_settings/3_general.md), [Login](../3_admin_settings/6_login.md), and [Legal Pages](../3_admin_settings/22_3_legal_pages.md).
2. Create and test a [Provider](../3_admin_settings/13_llm_providers.md) and [Model](../3_admin_settings/15_0_llm_models.md).
3. Review the default [Group](../4_user_group_management/5_group_settings.md).
4. Configure [HTTPS](3_setup_https.md), [Security](../3_admin_settings/22_0_security.md), and [Backups](../3_admin_settings/23_1_backups.md).
5. Test signup or invitation, sign-in, chat, upload, logout, and recovery with a normal User account.
6. Record the checks, backup owner, update window, and escalation path in the [operations runbook](4_operations.md).
