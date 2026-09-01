# Anthropic Base

Use **Anthropic Base** for a service that implements the Anthropic Messages interface at a custom **Base URL**. Use [Anthropic](4_anthropic.md) for Anthropic's hosted API.

Apply [Common Provider Settings](2_provider_settings.md) for shared fields and lifecycle rules.

## Configure

1. Confirm that the gateway supports model discovery and the Anthropic Messages features you need.
2. Create the provider with a clear **Name**, its **API key**, and the service's root **Base URL**.
3. Select **Test Connection** and save.
4. If discovery is unavailable, enter the exact model name manually when creating the model.
5. Pilot text-only chat before enabling files, reasoning, tools, caching, or web search.

Compatibility is the gateway operator's responsibility. A model name that resembles Claude does not prove feature parity. Keep unsupported options off and compare Omlorix errors with the gateway logs.

For private endpoints, ensure the Omlorix application service can resolve and reach the service, and review [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md). Use HTTPS across untrusted networks and avoid gateways that log prompts or secrets unexpectedly.
