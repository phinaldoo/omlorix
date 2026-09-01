# Models

**Admin Settings > Models** publishes provider-backed models to Omlorix users and controls their defaults, visibility, pins, and fixed skills.

## Create a Model

1. Select **New Model**.
2. Choose a provider or provider group.
3. Select the provider model and configure the displayed name, capabilities, and model options.
4. Save the model.
5. Set visibility and test it with an ordinary user.

Only enable capabilities that the selected provider model actually supports. A model can save successfully even when a provider later rejects an unsupported capability or option.

## Manage the Catalog

Use search and filters to review the catalog. You can duplicate a model, edit one model, or select multiple models for shared bulk changes. In a mixed bulk selection, leave a field unchanged unless it should be replaced for every selected model.

**Export Models** creates a versioned JSON snapshot of model rows. **Import Models** previews selected rows and creates them independently, so valid models can be created even when other rows fail.

Imported models receive fresh model IDs, but their source `provider_id` and embedded fixed-skill, tool/settings, user, and group references are carried without remapping. A row can import only when its referenced ordinary provider ID already exists on the destination. Provider imports create new local IDs, and provider groups have no matching import/export action. For a cross-instance transfer, create the destination dependencies first, replace every source relationship ID in a protected copy of the model JSON, import against concrete providers, and recreate provider-group assignments afterward. Re-select fixed skills and rebuild user/group visibility rather than trusting source IDs; follow the detailed [model import workflow](../6_llmmodels/2_manage_llmmodels.md#import-and-export).

The default model and default-pinned selections are instance settings and are not part of the model export. Set them only after imported or recreated models have passed access and provider tests, and review every per-row error before removing the source catalog.

## Defaults, Pins, and Visibility

- **Default model:** is selected when a user has no valid personal choice. A default must be visible to **Everyone**.
- **Default pinned models:** choose the initial picker set for users who have not customized their pins. Every selected model must be visible to **Everyone**.
- **Visibility:** choose **Everyone** or restrict the model to selected users and groups.
- **Fixed skill:** applies the selected skill to every generation with the model. Users cannot remove or replace it.

Omlorix limits the default pinned set to eight models. Once a user customizes pins, later default-pin changes do not overwrite that personal choice.

A model restricted to a group is usable only while the user has active membership in that group. Tool, skill, realtime, or provider availability can impose additional restrictions. Test access using a non-administrator account.

## Deletion and Dependencies

You cannot delete the current default model. Choose another eligible default first.

Deleting a model removes its usage statistics, user pins, and references from applicable rate-limit rules. A rate-limit rule with no remaining targets can be removed. Automations and Agents are moved to the default model when one exists; if a protected reference has no valid replacement, deletion is blocked. References in **Title generation model**, **Title generation model ID**, and **BYOK title generation model** are cleared or disabled as applicable.

Review the deletion preview rather than assuming a model is unused. Export configuration or create a backup before a large catalog cleanup.

## Related Settings

- [Providers](13_llm_providers.md)
- [Provider Groups](14_llm_provider_groups.md)
- [Rate Limits](16_rate_limits.md)
- [Skills](18_skills.md)
- [Dictation settings](15_1_dictation_settings.md)
- [Read aloud settings](15_2_text_to_speech_settings.md)
- [Realtime call settings](15_3_realtime_settings.md)
