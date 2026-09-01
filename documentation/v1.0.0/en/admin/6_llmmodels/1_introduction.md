# Models

A model is the user-facing AI configuration shown in Omlorix's model selector. It connects one [Provider](../5_llmprovider/1_introduction.md) model to Omlorix capabilities, instructions, access rules, and display information.

Think of the configuration in three layers:

1. **Provider** — the shared connection and credential.
2. **Model** — the name, behavior, capabilities, tools, and presentation users receive.
3. **Visibility and access policy** — whether everyone or selected users and groups can see it, plus the effective group limits and feature restrictions that govern its use.

## Recommended rollout

1. Create and test the provider.
2. Create one model with text-only capabilities and restrict its visibility to the pilot users or groups.
3. Test a normal chat, title generation, a long conversation, and error handling as an intended pilot user.
4. Add files, reasoning, web search, tools, or media only after the basic path is reliable.
5. Check statistics, provider billing, audit behavior, and user-visible labels.
6. Expand access and choose a **Default model** only after the pilot succeeds.

The provider's discovered catalog helps populate settings, but it is not proof of entitlement or feature support. Test the exact model and account. Local provider models may also depend on current server inventory and available memory.

## Access and lifecycle

- **Active** controls whether the model can be used.
- **Visibility** can be set to **Everyone** or restricted to selected users and groups. Effective group policy can impose additional limits even when the model is visible. An administrator's access does not prove a normal user's access.
- The **Default model** and every model in the instance's default pinned set must use **Everyone** visibility. The default pinned set accepts at most eight models.
- Provider synchronization can update status and availability. Keep **Auto-delete missing models** off unless the provider catalog is complete and authoritative.
- Deletion is blocked for the default model. Other deletion cleanup can change title generation, Agents, automations, presets, pins, and rate limits; replace feature-level media, research, and other saved selections before deleting.

Continue with [Manage Models](2_manage_llmmodels.md), [Model Settings](3_llm_model_settings.md), and [Leaderboard](4_leaderboard.md).
