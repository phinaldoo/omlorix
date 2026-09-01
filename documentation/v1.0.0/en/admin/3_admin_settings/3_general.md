# General

**Admin Settings > General** controls the instance name, brand assets, outbound request policy, and public browser origins.

## General Application Settings

**Application Name** appears throughout Omlorix, including sign-in, recovery, email, browser metadata, installed-app metadata, and passkey prompts.

Upload an **App Icon**, **Light Theme Logo**, and **Dark Theme Logo** with the controls on this page. Follow the displayed file type and size limits, then check the sign-in screen, main navigation, browser icon, and both appearance modes.

## Connection Settings

- **Offline Mode:** keeps local and private destinations available while blocking public internet destinations. It does not disconnect the host or replace a firewall.
- **External Requests Policy:** when Offline Mode is off, choose **Allow all outbound requests**, **Allow only local and private network targets**, **Allow only configured allowlist targets**, or **Block all outbound requests**.
- **External Request Allowlist:** add only the hosts, wildcard domains, URLs, or network ranges required by the allowlist-only policy.
- **Internet Connectivity Check Enabled:** enables the connectivity card on Dashboard. Offline Mode turns this check off.

Review [Outbound Network Access](3_1_outbound_network_access.md) before restricting a live instance.

## Public URLs

Enter complete origins such as **https://chat.example.com**. Do not add a path, query, fragment, or credentials. The first entry is the primary origin for generated links; every listed origin is accepted for sensitive browser authentication requests.

Public URLs affect sign-in, password reset, passkeys, OAuth, enterprise SSO, managed connections, and sharing. After adding, editing, removing, or reordering entries, restart Omlorix.

To change the primary origin safely:

1. Add the new origin while retaining the old origin.
2. Move the new origin to the first position, save, and restart.
3. Update callback or redirect registrations at identity and connection providers.
4. Test sign-in, passkeys, password reset, OAuth/SSO, connections, and sharing.
5. Remove the old origin and restart only after clients have moved.

If a browser reports a cross-site request, compare its exact scheme, host, and port with the entries on this page.
