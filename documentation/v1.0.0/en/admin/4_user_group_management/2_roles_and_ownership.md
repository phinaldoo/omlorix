# Roles and Instance Ownership

Omlorix has one protected **Owner** and three other account roles.

| Role | Purpose |
|---|---|
| **Owner** | Full administration plus authority to grant or remove Admin access. There can be only one. |
| **Admin** | Routine instance administration and management of User and Pending accounts. Cannot manage the Owner or another Admin. |
| **User** | Normal Omlorix use, controlled by group and feature settings. |
| **Pending** | Account exists but cannot use normal authenticated features until approved. |

On a new empty instance, the first account becomes Owner. Complete first-run creation with the intended person before enabling automated signup or provisioning. There is no normal **Transfer Owner** action.

## Owner-only actions

Only the Owner can:

- promote a User or Pending account to Admin;
- demote, lock, reset, delete, restore, or otherwise manage another Admin;
- import or recover data into an existing Admin account.

No one can change their own role from the Users table, assign another Owner, or administratively demote or delete the Owner. Omlorix also prevents changes that would leave no active administrative account.

## Change a role

1. Open **Admin Settings → Users**.
2. Find the account and use its **Role** control.
3. Confirm the intended **Pending**, **User**, or—when signed in as Owner—**Admin** role.
4. Verify the account's status, group, and authentication method.
5. Review the audit record and test access.

Admin access is instance-wide and is not limited by group membership. Use delegated group management when someone needs to manage only a group.

## Delegated group roles

Group **Owners**, **Managers**, and **Coordinators** are not account roles and do not grant Admin Settings access. They apply only to an assigned group and its descendants. See [Group Management](5_group_settings.md#delegated-management).

External signup, LDAP, SSO, and SCIM provisioning cannot grant Owner or Admin authority. Use external group synchronization for ordinary access, then have the Owner grant the small number of intentional Admin roles in Omlorix.

## Operational guidance

- Protect the Owner with a unique password, two-factor authentication, and a tested recovery path.
- Use a separate Admin account for routine work.
- Never share the Owner account; shared credentials weaken attribution and recovery.
- Review role, status, password, two-factor, import, deletion, and restore events.
- Before restoring a backup, confirm which account is Owner, preserve its authentication dependencies, and verify that its sign-in method still works after recovery.

If the Owner cannot sign in, recover that account through a configured sign-in recovery method. Do not promote a replacement by importing or provisioning an external administrator. Emergency database repair is an offline deployment-owner procedure requiring a verified backup, preserved evidence, and careful validation before restart.
