# Enterprise SSO

**Admin Settings > Enterprise SSO** groups **SAML 2.0** and **OpenID Connect** under **Identity Providers**, and **SCIM 2.0** under **User Provisioning**. Complete and test a local Owner account before giving an external identity system control of access.

## SCIM 2.0

Configure the identity provider's SCIM base URL as the instance's exact public origin followed by `/api/v1/scim/v2`, for example `https://chat.example.com/api/v1/scim/v2`. Send the configured token as an `Authorization: Bearer …` credential. The endpoint exposes SCIM discovery plus **Users** and **Groups** resources; test discovery, lookup, create, update, disable, group membership, and deletion with controlled ordinary accounts before rollout.

- **Enable SCIM 2.0** enables authorized provisioning.
- **SCIM Bearer Token** is the current credential.
- **Previous SCIM bearer token** supports a short overlap during rotation; clear it after the identity provider changes.
- **Link existing users by email** permits linking to a matching local account.
- **Sync SCIM group memberships** maintains SCIM-managed membership and active-group behavior.
- **Default User Role** and **Default User Group** are fallbacks.

Use a unique high-entropy token and place it only in the identity provider's protected credential field. SCIM can provision **User** or **Pending** accounts; it cannot grant **Admin** or **Owner**.

A SCIM user `DELETE` starts Omlorix's normal account-deletion and retention workflow; it is not a promise of immediate physical erasure. SCIM group deletion removes the managed Omlorix group, so test the identity provider's deprovisioning behavior and preserve a recovery path before enabling destructive synchronization.

## SAML 2.0

Configure **Entity ID (SP Entity ID)**, **Identity Provider SSO URL**, and **Identity Provider X.509 Certificate**, then set **SAML Button Text**, **Allowed Email Domains**, **Enable JIT Provisioning**, **Default User Role**, **Default User Group**, and **SAML Attribute Mapping**.

**SAML security settings** covers identity-provider identity, certificate rotation, NameID behavior, and signed requests. **SAML identity policy** covers account linking, profile synchronization, required upstream groups, and mappings. Review and validate both advanced JSON fields before saving.

Plan certificate rotation before the old certificate expires. Test both old and new signing material during the approved overlap.

## OpenID Connect

Prefer **Discovery URL (Optional)** with a validated **Issuer (Optional)**. When discovery is not used, configure **Authorization Endpoint**, **Token Endpoint**, **JWKS URI (Optional)**, and **UserInfo Endpoint (Optional)** as required.

Set **OIDC Scopes**, **OIDC Button Text**, **Allowed Email Domains**, **Enable JIT Provisioning**, **Default User Role**, **Default User Group**, and **OIDC Attribute Mapping**. The required **openid** scope cannot be removed, and identity or verified-email claims cannot be remapped.

**OIDC protocol settings** controls token authentication and optional authorization prompts. **OIDC identity policy** controls linking, profile synchronization, required groups, and mappings.

Use **Test OIDC configuration** before enabling login. It checks the displayed configuration and discovery/signing requirements without revealing stored secrets.

## Identity and Provisioning Rules

- Browser-based JIT creation needs both global **Enable sign-up** and the provider's **Enable JIT Provisioning**.
- Automatic role assignment is limited to **User** and **Pending**.
- Login-time synchronization does not replace an existing Owner or Admin role.
- Enable email linking only when the upstream system verifies and controls the address.
- Domain and required-group settings are access controls; test both allowed and denied users.
- Account locks, legal acceptance, 2FA, deletion state, and group access windows still apply after SSO succeeds.

## Rollout and Diagnostics

1. Configure one identity provider with JIT off.
2. Verify the public origin, callback, issuer or certificate, signing keys, and claims.
3. Link a controlled ordinary user.
4. Enable JIT with **Pending** as the default when approval is required.
5. Test group mappings, domain and required-group denial, disabled accounts, legal acceptance, and 2FA.
6. Use **Authentication diagnostics > Refresh failures** and the displayed time, reference, provider, stage, and cause for support. Never copy assertions, tokens, or secrets into a ticket.

Keep a tested local Owner path throughout rollout and credential rotation.
