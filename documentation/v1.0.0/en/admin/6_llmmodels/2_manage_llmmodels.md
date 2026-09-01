# Manage Models

Open **Admin Settings > Models** to create, duplicate, edit, import, export, or remove chat models.

## Create a model

1. Select **New Model**, then **Create Model**.
2. Choose the **Provider** and a discovered provider model, or enter the exact model name when manual entry is allowed.
3. Enter a clear user-facing **Name** and optional description or icon.
4. Configure only verified capabilities. See [Model Settings](3_llm_model_settings.md).
5. Restrict visibility to the pilot users or groups and choose whether the model is **Active**.
6. Save and run a real chat as both an administrator and an intended normal user.

A provider connection test does not validate a model. Test the longest expected input, enabled file types, reasoning, every selected tool, and common failures.

## Defaults and visibility

Set the **Default model** only after its provider, access rules, and quotas are stable. The default model and every model in the administrator's default pinned set must be visible to **Everyone**; the default pinned set accepts at most eight models. A user's customized pins are not overwritten by later changes to that default set.

When a user cannot see a model, check:

- the model is **Active**;
- its provider still exists and is reachable;
- the model's direct user and group visibility assignments;
- the user's effective group policy and access window;
- model or group usage limits;
- whether the model is available to the provider account and region.

## Duplicate and bulk actions

Use **Duplicate** to create a safe variant before changing tools, instructions, or access. Give the copy a distinct name and retest it; provider capabilities and secrets are not validated merely by copying.

Bulk edit is useful for shared presentation or access changes. Review the selected rows and fields carefully because the action applies the same value to every selected model. Bulk deletion is permanent, and the deletion safeguards below apply to every selected model.

## Import and export

Use **Export All** and **Import Models** on **Admin Settings > Models**. The version 1.0 JSON bundle includes each model's source ID, display fields, exact `provider_id`, provider model identifier, settings, capabilities, tools, access, status, active state, and creation time. It does not include providers, provider credentials, provider groups, Web Search providers, MCP servers, custom Python tool definitions, Skills, users, groups, defaults, or default pins.

Import always creates fresh model IDs; it never updates an existing model or preserves the source model ID. Each entry must reference a destination provider whose ID exactly equals its exported `provider_id`, and that provider's type must match the exported provider type. The importer does not match providers by name and does not translate the fresh IDs created by a provider import.

For a transfer to another instance:

1. Import and verify [providers](../5_llmprovider/1_introduction.md#import-and-export-providers). Export providers from the destination and map each new `data.providers[].id` by its unique `name` and `provider` values.
2. Restore Web Search providers, administrator MCP servers, custom Python tools, Skills, users, and groups that the models require, recording every changed ID.
3. Make a protected copy of the model JSON. In every `data.models[]` entry, replace `provider_id` with the matching destination provider ID. Also replace or remove stale IDs in settings, tools, and access—for example Web Search providers, allowed MCP servers, title-generation models, fixed Skills, users, and groups.
4. Models assigned to a provider group need special handling: provider groups are not included in the provider bundle and their IDs are not accepted as direct providers by model import. Point the import copy at a compatible concrete provider, recreate the destination provider group, and reassign the model afterward.
5. Configure and verify a destination default before import. If no default exists, also set `access.everyone` to `false`; the first Everyone-visible model can become the global default even when `is_active` is `false`.
6. In the protected copy, set `is_active` to `false` and restrict access. Import selected entries, read every per-item error, verify access and dependencies, test, and then activate deliberately.

Provider settings are schema-validated. Tool selections and access lists are normalized and carried without validating or remapping referenced records; review or reselect every referenced Web Search provider, MCP server, custom Python tool, Skill, user, group, and model on the destination. Capabilities are recalculated from the imported provider settings, tools, and existing capability data. Because imported models receive fresh IDs, references between models—such as a specific title-generation model—must also be reselected after import. Never add credentials to a model export.

After import:

1. Read the result summary and resolve skipped items.
2. Check provider assignments, direct users and groups, Web Search, MCP, Skills, tools, and title generation.
3. Recreate provider-group routing and set the default and default pins separately.
4. Keep imported models inactive or restricted until real requests pass.

## Provider synchronization and deletion

Provider refresh compares saved models with the provider's current list. **Auto-delete missing models** should be enabled only when that list is complete and stable. An outage, permission change, or renamed deployment can otherwise remove valid configurations.

Before deleting a model, find and replace its use in Deep Research, speech/media, and other feature settings. Omlorix applies these model-record safeguards:

- the default model cannot be deleted;
- title-generation references are cleared, and dependent title generation that selected this specific model is disabled;
- model-setting presets, default and user pins, and model-specific rate-limit references are removed;
- Agents and automations are moved to the default model when one exists. If either still references the model and no default is configured, deletion is blocked.

Deleting the model does not delete the provider-side model or historical chat content. Provider deletion invokes the same safeguards while attempting to remove all of that provider's models.
