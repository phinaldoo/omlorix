# LDAP

**Admin Settings > LDAP** adds direct LDAP or Active Directory credential authentication to the normal sign-in form, with optional just-in-time provisioning and group synchronization.

## LDAP Authentication and Connection

- **Enable LDAP login** activates directory authentication.
- **Login label** and **Identifier hint** explain the directory option to users.
- **Server URIs** is an ordered failover list. Use TLS or StartTLS endpoints.
- Keep **Validate server certificate** enabled. Use the managed CA-certificate upload when the directory uses a private certificate authority.
- **Allow insecure plaintext LDAP bind** can expose bind and user credentials; use it only for isolated, temporary testing.
- Set **Connect timeout (seconds)** and **Receive timeout (seconds)** for the real network conditions.

The optional **Bind DN** needs only the permissions required for user and group searches. Store **Bind password** in its protected field and rotate it through a tested maintenance procedure.

## User Search

Configure **User base DN**, **User search filter**, **User search scope**, the profile attributes, **Directory login attribute**, and **Stable user ID attribute**.

Use an immutable directory value for **Stable user ID attribute**, not an email address or renamable username. A provisioned user also needs a usable email address. Test identifiers containing spaces or special characters as well as an ordinary account.

## Provisioning and Sync

- **Enable JIT provisioning** creates an account at first successful LDAP sign-in; global **Enable sign-up** must also be on.
- **Link existing users by email** links a matching local user only after successful directory authentication.
- **Sync profile on login** and **Sync email on login** replace the corresponding local values.
- **Sync Omlorix group on login** and **Sync role on login** reapply mappings at every sign-in.
- **Default role** can be **User** or **Pending**.
- **Default Omlorix group** is used when no group mapping matches.

LDAP cannot grant Admin or Owner and cannot take over an unlinked administrative account. Enable email linking only when the directory verifies and controls that address.

## Group Synchronization

Turn on **Enable group synchronization**, then choose **Group source**:

- **User memberOf attribute** reads **Membership attribute** from the user entry.
- **Group search** uses **Group base DN**, **Group search filter**, **Group search scope**, and **Group name attribute**.

**Required LDAP groups** is an access gate: a user must match at least one entry when the list is populated. **LDAP group to Omlorix group** and **LDAP group to role** are ordered mappings; role targets are limited to **User** and **Pending**.

Before turning off group synchronization, review and disable dependent group or role sync controls and remove any required-group gate you no longer intend to enforce. Hidden values can remain saved.

## Rollout

1. Keep a local Owner sign-in available.
2. Validate directory reachability and certificate trust from the Omlorix server.
3. Test bind and user search with JIT and synchronization off.
4. Add group resolution and test one allowed and one denied user.
5. Enable JIT, profile sync, group sync, and role sync one at a time.
6. Check Admin Notifications and sanitized server logs after each step.

For failures, check DNS, certificate trust, bind permissions, base DNs, filters, stable identifiers, required groups, global signup, and outbound policy. Never log a user's password or the bind credential.
