# Providers

**Admin Settings > Providers** manages the services that supply chat and related model capabilities.

## Add or Edit a Provider

1. Select **Add Provider** or edit an existing row.
2. Choose the provider type and enter a clear **Name**.
3. Enter the displayed endpoint and protected credential fields.
4. Configure the provider-specific options and save.
5. Use the row test before assigning production models.

For local providers such as **Ollama** and **LM Studio**, refresh or inspect the available model inventory after the service is reachable. A successful provider test confirms connectivity and credentials; it does not guarantee that every model supports every Omlorix feature.

Use a separate provider entry when credentials, network location, data handling, or operational ownership differ. Treat provider exports as sensitive even though protected API keys and custom-header values are not included.

## Local Model Management

**Model Management** is available when supported by the local service:

- Ollama offers **Download model**, **Load model**, **Unload model**, **Delete model**, and **Loaded Models**. Deletion permanently removes the provider model from its disk.
- LM Studio offers **Download model**, **Load model**, **Unload model**, **Installed Models**, and **Loaded Models**, with optional download and runtime choices displayed by the service.

These actions change the local provider service. They do not automatically reconcile the Omlorix [Models](15_0_llm_models.md) catalog. Before unloading or deleting a provider model, find every Omlorix model that uses it; afterward, refresh the inventory and test those entries.

## Find, Move, and Reuse Configuration

Use search and provider-type filters to narrow the table. **Export Providers** creates a versioned JSON file. **Import Providers** previews selected rows and creates each provider independently, so a name conflict or invalid row can produce partial success without rolling back providers already created.

API keys and custom-header values are redacted from exports. For a provider type that requires a key, enter a fresh API key in the import preview or that row is rejected. Optional-key providers can be completed afterward; custom-header values must be re-entered after import. Test every created provider. Imported providers receive new local IDs; the import does not preserve or remap source IDs in model exports, provider groups, or other configuration. Recreate those references on the destination and review the result before deleting the source configuration.

Before importing into another environment, confirm that its outbound-access policy permits the same endpoints and that its users are allowed to send data to those services.

## Understand Access

Users do not receive access merely because a provider exists. A usable chat model also needs:

- an enabled model that points to the provider or one of its provider groups
- model visibility that includes the user
- any required group membership and access window
- an applicable rate-limit allowance

For the complete access chain, see [Models](15_0_llm_models.md), [Provider Groups](14_llm_provider_groups.md), and [Rate Limits](16_rate_limits.md).

## Delete a Provider Safely

Deleting a provider can affect more than the provider row. The confirmation shows the expected impact:

- models that point directly to the provider are deleted
- the provider is removed from provider groups
- a group that can no longer operate with at least two providers is deleted with its models
- a surviving group is updated to use its remaining providers

Deletion can be blocked when the resulting model changes would leave an unresolved default or another protected dependency. Review the impact, choose a replacement default where needed, and move dependent models before retrying.

After any provider change, test a normal user request, streaming, tool use, file or image behavior, and any speech or realtime feature that uses the provider. Monitor [Admin Notifications](2_1_admin_notifications.md) for failures.
