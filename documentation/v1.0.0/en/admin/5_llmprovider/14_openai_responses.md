# OpenAI Responses API

Use **OpenAI Responses API** for a gateway or service that implements OpenAI-compatible Responses. Use native [OpenAI](12_openai.md) for OpenAI's hosted service and [OpenAI Chat Completions API](13_openai_chat_completions.md) for a Chat Completions endpoint.

Apply [Common Provider Settings](2_provider_settings.md) for shared fields and lifecycle rules.

## Configure

1. Confirm support for streaming Responses and every required input, reasoning, tool, and continuation feature.
2. Create the provider with a **Name**, **API key**, and root **Base URL**.
3. Add required **Custom headers**, test the connection, and save.
4. Choose a discovered model or enter the exact model name when discovery is unavailable.
5. Test text-only chat first, then enable advanced capabilities individually.

Provider model names may cause Omlorix to show settings commonly associated with that model family. Treat these as configuration options, not proof of gateway support.

**Store responses**, reasoning continuation, prompt caching, service tiers, native tools, and safety identifiers are meaningful only if the compatible service implements them. Confirm upstream storage and logging behavior independently; these controls do not change Omlorix's own history and retention.

This provider can also be selected for transcription, text-to-speech, image generation, or video generation only if the service implements the corresponding OpenAI-compatible endpoints. Configure and test each feature independently; a successful Responses chat does not validate those service endpoints.

For private endpoints, verify reachability from the Omlorix application service, [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md), TLS, authentication, and retention. When troubleshooting, return to text-only input with optional controls disabled.
