# Login

**Admin Settings > Login** controls local account entry points, two-factor authentication, passkeys, email delivery, registration defaults, legal gates, and password policy.

## Authentication Entry Points

- **Enable sign-in for users:** controls ordinary user sign-in. Administrators can still sign in for recovery.
- **Enable sign-up:** permits creation of new accounts. Provider-specific signup and JIT controls can narrow this further.
- **Enable password reset:** shows the reset workflow; **Email delivery** must also be configured and working.
- **Enable two-factor authentication:** enables the selected **2FA provider** for enrolled users.
- **Force 2FA enrollment:** prevents users from continuing until they enroll.
- **Support email:** supplies the contact shown when a user needs sign-in help.

**2FA provider** supports **Authenticator App (TOTP)** or **Email OTP**. The email option also uses **OTP length**, **OTP lifetime (seconds)**, **Resend cooldown (seconds)**, and **Max OTP attempts**.

For account recovery, an administrator can use **Edit User > Reset 2FA** for another locally managed account. The action is audited and requires an access reason; see [Users](4_1_users.md).

## Email Delivery

Configure **From address**, **SMTP host**, **SMTP port**, and any required **SMTP username** and **SMTP password**. Select **Use SMTP STARTTLS** or **Use SMTP SSL** according to the mail service.

Test delivery before enabling password reset, email OTP, forced enrollment, verified email changes, or workflows that depend on security notices. A visible reset link does not prove that mail can be sent. Use a dedicated mail account and never place the SMTP password in a normal text field. SMTP requires TLS or SSL by default, and Omlorix verifies the server certificate and hostname. A trusted local plaintext relay can be used only with the explicit `EMAIL_ALLOW_INSECURE_SMTP=true` environment override; this permits only unauthenticated SMTP, sends message contents and one-time secrets without transport encryption, and must never be used across an untrusted network.

Omlorix stores system messages in an encrypted database outbox and delivers them through the separate, always-on `email_worker` service. Password-reset and one-time-code messages are revalidated immediately before delivery so superseded or expired credentials are not sent. Security notices cover password, email, passkey, two-factor, connected sign-in method, new-device, deactivation, and deletion events. Monitor worker health, queue depth, oldest-message age, retry/dead outcomes, and SMTP connectivity; multiple worker replicas can share the queue safely.

## Legal Acceptance

- **Show privacy notice link** and **Show terms of service link** control the login footer.
- **Require terms acceptance during signup** requires the current Terms revision during new-account creation.
- **Block app access until terms are accepted** requires existing signed-in users to accept the current revision before using the application.

Publish and verify the current documents under [Privacy Policy and Terms of Service](22_3_legal_pages.md) before enabling enforcement.

## Passkeys

**Enable passkeys** permits WebAuthn sign-in. Passkeys require HTTPS and a stable primary **Public URLs** origin. Test enrollment and sign-in on every supported public origin before changing other sign-in methods.

## Registration Defaults and Password Policy

- **Allowed sign-up domains:** leave empty to accept any domain, or enter the permitted email domains.
- **Default user role:** new accounts can start as **User** or **Pending**. Registration cannot create administrative authority.
- **Password Policy Requirements:** set minimum length and required special, uppercase, lowercase, and number characters.

Choose **Default user group** under [Groups](5_groups.md). Identity-provider role, group, domain, workspace, organization, or tenant rules can further restrict account creation.

A password-policy change applies when a password is next created or changed; it does not rewrite existing passwords.

## Safe Rollout

1. Keep a tested Owner sign-in and console recovery path.
2. Test email before enabling email-dependent features.
3. Publish the legal documents before enabling acceptance gates.
4. Test signup, sign-in, password reset, 2FA, passkeys, and logout with an ordinary user.
5. Enable forced enrollment or disable an entry point only after the replacement path succeeds.
