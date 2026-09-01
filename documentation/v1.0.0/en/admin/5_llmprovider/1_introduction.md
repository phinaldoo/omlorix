# Providers

A provider connects Omlorix to an AI, speech, or media service. Providers hold shared credentials and connection settings; [models](../6_llmmodels/1_introduction.md) define what users can select in chat.

## Add a provider

1. Open **Admin Settings > Providers** and select **Add Provider**.
2. Choose the provider type and enter a clear **Name**.
3. Complete the visible fields using [Common Provider Settings](2_provider_settings.md) and the provider-specific guide below.
4. Select **Test Connection**.
5. Save the provider, then create at least one [model](../6_llmmodels/2_manage_llmmodels.md).
6. Test a real chat and every optional feature you intend to enable. A successful connection test usually checks discovery, not generation, tools, files, speech, or media.

Choose a native provider type when one is available. Use **OpenAI Chat Completions API**, **OpenAI Responses API**, or **Anthropic Base** only for a compatible gateway or service. A compatible endpoint is not interchangeable with a native provider merely because model names look alike.

## Operate providers safely

- Use a dedicated, least-privilege credential with provider-side budgets and alerts.
- Keep **Auto-delete missing models** off until you trust the provider's model list. Temporary provider outages or incomplete discovery must not remove production models.
- **Disable regular provider requests** stops background discovery and status checks; it does not block user requests.
- Treat **Status** as the last discovery result, not continuous health monitoring.
- After changing a credential, endpoint, region, or API version, test discovery and an actual request before restoring broad access.
- Review every provider in the [Processor & Transfer Register](../3_admin_settings/22_5_processor_transfer_register.md), including subprocessors selected by routing services.

## Import and export providers

Use **Export All** and **Import Providers** on **Admin Settings > Providers** for the versioned provider JSON bundle. This is a configuration transfer, not a complete backup:

- exports include each provider's type, name, icon, non-secret settings, source ID, status snapshot, and credential-presence metadata;
- API keys are never exported, and custom-header values are replaced with redacted placeholders;
- the import dialog asks for a new API key for provider types that require one. Ollama, LM Studio, and Anthropic Base can be imported without a key; if one of those connections used a key, edit it after import;
- re-enter every custom header after import, even when its header name appears in the export;
- import creates providers with fresh IDs and an **Unknown** status. It does not update an existing provider or restore provider-group membership, model assignments, speech/media selections, or other ID-based references;
- duplicate names and invalid entries are reported per item. Read the complete result and test every created provider.

For a migration, import providers before models. Then export providers from the destination and map each new `data.providers[].id` by its unique `name` and `provider` values. The model importer requires exact destination provider IDs; see [Import and export models](../6_llmmodels/2_manage_llmmodels.md#import-and-export).

## Delete a provider

Deleting a provider also attempts to delete every model owned by it and can remove it from provider groups when confirmed in the deletion workflow. Model safeguards still apply: the default model cannot be deleted, and Agents or automations block deletion when they reference a model and no default model exists. When a default exists, those Agent and automation references are migrated to it.

Provider deletion is not atomic: child models are deleted and committed one at a time. Preflight every child model before confirming deletion. If deletion fails partway through, refresh and verify both the provider and the complete model catalog before resolving the blocker and retrying.

Before deletion, replace the default, review provider groups, model title-generation references, pins, rate limits, Agents, automations, speech and media settings, Deep Research, and other feature selections. Deleting the Omlorix record does not revoke its upstream credential or delete the provider-side account, deployments, or historical chat content.

## Provider guides

- Hosted AI: [OpenAI](12_openai.md), [Anthropic](4_anthropic.md), [Google AI Studio](8_google_aistudio.md), [OpenRouter](15_openrouter.md), [xAI](17_xai.md)
- Microsoft: [Microsoft Azure](10_microsoft_azure.md)
- Local or private: [Ollama](11_ollama.md), [LM Studio](9_lmstudio.md)
- Speech: [ElevenLabs](7_elevenlabs.md)
- Compatible services: [Anthropic Base](5_anthropic_base.md), [OpenAI Chat Completions API](13_openai_chat_completions.md), [OpenAI Responses API](14_openai_responses.md)
- Personal credentials: [Bring Your Own Key](byok.md)
