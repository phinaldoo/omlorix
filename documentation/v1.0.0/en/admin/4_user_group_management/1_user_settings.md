# Edit a User's Settings

Open **Admin Settings → Users**, then select **Edit user**. Enter a specific **Access reason** such as a support ticket or lockout investigation. The reason is audited; do not include secrets or unrelated personal information.

An Admin can manage ordinary User and Pending accounts. Only the Owner can manage another Admin. The Owner account's role and status are protected; its signed-in user can edit only their own permitted settings. See [Roles and Instance Ownership](2_roles_and_ownership.md).

## User Profile

The profile contains:

- **Personal Information**: **Email**, **First Name**, and **Last Name**.
- **Group & Role**: **Group** assignment. Change account role from the Users table, not this editor.
- **Security**: **New Password**, **Failed Sign-in Attempts**, and **Reset Two-Factor Authentication** where permitted.
- **Account Lock**: **Account Locked**, **Lock Until**, **Lock Type**, and **Lock Reason**.

Changing **Email** or setting **New Password** requires your recent security verification with an available password, enrolled 2FA method, or passkey. A freshly authenticated session is accepted only when you have no usable step-up factor.

An administrative email change takes effect immediately, signs the user out, invalidates pending password-reset, email-change, and one-time authentication actions, and sends security notices to the old and new addresses. Confirm the destination before saving; this bypasses the user's verification-link workflow.

A new administrative password signs the user out everywhere, invalidates other password-reset links, pending email changes, and one-time authentication state, and sends a security notice. Leave **New Password** empty to keep the current password and deliver any replacement through a secure channel.

### Unlock an account

1. Set **Failed Sign-in Attempts** to `0`.
2. Turn off **Account Locked**.
3. Clear **Lock Until**, **Lock Type**, and **Lock Reason** when they no longer apply.
4. Save and ask the user to sign in again.

A failed-attempt block and a manual account lock are separate conditions. Also check role, status, group access windows, IP policy, and the configured sign-in method if access still fails.

### Reset Two-Factor Authentication

Use this only after verifying the person's identity. Save or discard unrelated edits first, then select **Reset Two-Factor Authentication** and confirm. The user must enroll again. An Admin cannot reset another Admin or the Owner; only the Owner can recover another Admin.

## Preference pages

The editor can also show the user's current **Security**, **General**, **Appearance**, **Chat**, and **Two-Factor Authentication** preferences. Memory availability and model choice are group settings, while users inspect their stored facts under **Workspace > Memories**. Authentication-linked accounts can add diagnostic pages such as **Social Login**, **SSO Login**, **SCIM**, or **LDAP Login**. **Secrets** and **User State** are recovery and investigation views; do not change them during ordinary preference support.

Available fields depend on the user's group, sign-in source, models, and other settings. Directory-managed identity fields can be hidden or overwritten by the next synchronization. Change those at the identity provider unless a documented recovery procedure says otherwise.

Important effects:

- Sidebar and model-selector preferences affect presentation, not group permissions.
- Enabling a user preference cannot grant a feature disabled for the group.
- Changing profile visibility or personal-information access affects what the assistant can use; confirm the support request authorizes it.
- Changing setup, authentication linkage, or diagnostic state can interrupt sign-in. Avoid it unless you understand the recovery consequence.

Changed pages are marked until **Save Changes**. Before saving, reread the marked-page summary so an unrelated diagnostic field is not changed accidentally. If saving fails, reopen the user and verify the effective values before retrying. Omlorix sends security notices for administrative email and password replacement, but not for ordinary lock or preference edits; communicate those separately when policy requires it.
