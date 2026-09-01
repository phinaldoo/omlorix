# Create One User

Use **Admin Settings → Users → Add user** for one local account. Use [Bulk Import Users](4_bulk_create_users.md) for a prepared list, and use your configured identity provider for directory-managed accounts.

## Before creation

- Configure the destination group and confirm its models, features, access windows, file limits, and retention.
- Decide how the temporary password will be delivered securely.
- Confirm a local password is the intended sign-in method.

This workflow creates the account immediately and does not email credentials. Confirm the address before saving; creating a second spelling is not a safe way to repair an existing identity.

## Create the account

1. Enter **First name**, **Last name**, **Email address**, and a policy-compliant **Password**.
2. Select the correct **Group**.
3. Turn on **Require password change** for an administrator-issued password.
4. Select **Create User**.
5. Verify the new user's group, role, and status on the Users page.
6. Send the login address and temporary password through protected channels, preferably separately.
7. Ask the user to replace the password and complete required setup and two-factor enrollment.
8. Confirm successful sign-in, correct group access, and the ability to reach the **Default model**.

The account uses the configured default role. If it is **Pending**, approve it before expecting normal access. Do not use Admin as a substitute for group permissions; only the Owner should grant administrative authority.

## Troubleshooting

- **Email already exists:** find the existing active, pending, inactive, or deleted account and recover or update it instead of creating a near-duplicate.
- **Password rejected:** review the password requirements under **Admin Settings → Login**.
- **No groups available:** create and configure a group first.
- **User cannot sign in:** check role, status, account lock, failed attempts, password-change requirement, group access window, IP policy, login method, and two-factor setup.

Never put a temporary password in a public ticket or reuse it across accounts.
