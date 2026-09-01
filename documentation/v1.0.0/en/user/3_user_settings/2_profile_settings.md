# Profile Settings

Open **Settings > Profile** to manage your account details.

- **Profile Picture:** upload a picture or remove the current one. If a connected sign-in provider supplied the picture, the page identifies it.
- **Personal Information:** edit **First Name**, **Last Name**, and **Email Address**, then select **Save Changes**. Name changes save immediately. Starting an email change requires recent identity verification, and system email must be configured.
- **Delete Account:** deactivate or permanently erase the account according to the policy and retention notice shown in the confirmation. This action also requires recent identity verification.
- **Log Out:** end the current browser session. Other sessions remain active until you end them under Security.

Your picture and name may appear to people you collaborate or share with. Avoid sensitive information in either.

## Change your email safely

Your current address remains the sign-in address until you confirm the new one. Omlorix sends a verification link to the new address and a cancellation link to the current address. Both links open on the sign-in page and expire after 24 hours. Starting another email change cancels the earlier pending request.

Confirming changes the address. Cancelling leaves the current address unchanged. Either terminal action signs out every current session, invalidates outstanding password-reset links and one-time authentication actions, and requires you to sign in again. If you did not request the change, use the cancellation link promptly and investigate the account.

If Omlorix has no local password, passkey, or two-factor method with which to confirm a sensitive change, sign in again and retry it. Temporary accounts cannot change personal details or delete themselves. If the page says **Managed by your organization**, identity and sign-in changes must be handled by the organization. Group policy can also disable name changes, email changes, or self-deletion separately.

## Delete or deactivate the account

Before deleting an account, [download important data](../6_privacy_data/2_data_controls.md), review [Shared Items](../6_privacy_data/3_shared_items.md), and disconnect external services. Read the exact effect shown in the confirmation:

- **Erase Account Now** permanently removes the live account and associated application data immediately. It cannot be restored.
- **Deactivate Account** signs the account out and makes it inactive immediately. With scheduled deletion, an administrator can restore it only until the displayed permanent-erasure time. With indefinite retention, it remains restorable by an administrator until permanently erased later.

Deactivation removes active sign-in sessions and pending recovery/security actions. Authentication logs, audit logs, and server backups follow separately configured retention and erasure controls; a self-service account archive is not a server-backup deletion request.

Account deletion cannot recall information already copied by another person or processed by an external service. The instance owner account cannot self-delete, and administrator-account deletion requires owner authorization. Passwords, passkeys, two-factor authentication, and sessions are under [Security](3_security_privacy.md).
