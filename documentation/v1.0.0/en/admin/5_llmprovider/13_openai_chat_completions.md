# OpenAI Chat Completions API

Use **OpenAI Chat Completions API** for a gateway or service that implements OpenAI-compatible Chat Completions. Prefer a native provider when one matches the service; use [OpenAI Responses API](14_openai_responses.md) for a Responses-compatible endpoint.

Apply [Common Provider Settings](2_provider_settings.md) for shared fields and lifecycle rules.

## Configure

1. Confirm that the service supports streaming chat, usage reporting if required, and the optional features you plan to enable.
2. Create the provider with a **Name**, **API key**, and root **Base URL**.
3. Add **Custom headers** only when the gateway requires them.
4. Select **Test Connection** and save.
5. Choose a discovered model or enter the exact model name when discovery is unavailable.
6. Pilot text-only chat, then test tools, files, reasoning, and generation controls one at a time.

Compatibility varies widely. A successful model-list test does not prove chat compatibility, and a familiar model name does not guarantee the behavior of the native service. Leave unsupported settings blank.

This provider can also be selected for transcription, text-to-speech, image generation, or video generation only if the service implements the corresponding OpenAI-compatible endpoints. Each feature has separate administrator settings and must be tested independently; chat compatibility proves none of them. For private endpoints, review [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md), TLS, authentication, request logging, and retention.

If chat fails, compare the saved model name and enabled features with the gateway documentation and logs. Start again with a small text-only request before increasing timeouts. Do not enable a native-only option merely because it appears for a familiar model family.
