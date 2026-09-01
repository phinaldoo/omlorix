# Common Provider Settings

This is the canonical reference for fields shared across provider forms. The exact form depends on the provider type; configure only settings your provider or gateway explicitly requires.

| Setting | Use |
| --- | --- |
| **Name** | A unique operator-facing name, preferably including account, region, or purpose. |
| **API key** | A dedicated service credential. Re-enter it only when rotating the secret. |
| **Base URL** or **Endpoint** | The service root shown in the provider's documentation. Do not paste a model-specific request URL. |
| **Organization** / **Project ID** | Optional provider account scope. Project-scoped credentials remain the stronger boundary. |
| **API version** | Use only when required for the selected service and features. |
| **Custom headers** | Additional gateway headers. Do not duplicate normal authentication here. |
| **Disable regular provider requests** | Stops background discovery and status refresh, not generation. |
| **Notify model changes** | Warns administrators when the discovered catalog changes. |
| **Auto-delete missing models** | Removes models absent from a successful provider refresh. Enable only for an authoritative, stable catalog. |

The lifecycle fields in the last three rows are shown only for provider types that support them. Provider request timeouts use Omlorix's application-level safety limit and are not configurable per provider.

**Test Connection** tests the unsaved form where supported. A warning may mean that model discovery is unavailable even though a manually entered model can work. Always test a saved model with a small real request.

For a custom or private endpoint, confirm that the Omlorix application service—not your browser—can reach it. Review TLS, DNS, firewall rules, [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md), authentication, data retention, and request logging before rollout.

Leave a saved secret field unchanged when the form indicates that a credential is already stored. Entering a placeholder or masked value can replace the working credential. After any secret, endpoint, account, or version change, test discovery and one real request before restoring broad model access.

Provider exports omit API keys and redact custom-header values. Follow the complete restore and ID-remapping procedure in [Providers](1_introduction.md#import-and-export-providers); a provider export is not a credential backup.
