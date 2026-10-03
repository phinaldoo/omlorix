# Anthropic

Use **Anthropic** for Anthropic's hosted Claude API. For a compatible gateway or another endpoint, use [Anthropic Base](5_anthropic_base.md).

Apply the shared credential, discovery, import, and deletion rules in [Common Provider Settings](2_provider_settings.md); this page covers Anthropic-specific rollout.

## Configure

1. Create a dedicated Anthropic API key and set provider-side budgets and alerts.
2. Open **Admin Settings > Providers**, select **Add Provider**, and choose **Anthropic**.
3. Enter the **Name** and **API key**, then select **Test Connection** and save.
4. Create a [model](../6_llmmodels/2_manage_llmmodels.md) from the discovered list.
5. Start with text chat, then enable and test only the files, reasoning, tools, native web search, or other capabilities supported by that exact model.

Model availability and capabilities vary by account, region, and provider rollout. Discovery does not prove that a request is permitted. Review Anthropic's current retention and data-use terms, especially before enabling attachments, tools, or native web search, and use provider billing as the cost authority.

If discovery succeeds but chat fails, verify the selected model, account entitlement, balance, regional access, and enabled model features before rotating the key. Rotating a credential rarely fixes a capability or entitlement mismatch.

## October 3, 2026 catalog review

[Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) (`claude-sonnet-5-5`) is recognized for pricing, its June 2026 knowledge cutoff, and native web search. Standard estimates are $2 input, $0.20 cached input, and $10 output per million tokens. Discovery supplies its token limits and effort levels.

Sonnet 5.5 defaults to adaptive thinking and rejects manual budgets. Turning off upfront thinking sends `between_tools`, which is supported through high effort. Omlorix rejects that setting at xhigh or max effort; use adaptive thinking for those levels. See [Sonnet 5.5 request changes](https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5). [Sonnet 4.5 is deprecated](https://platform.claude.com/docs/en/about-claude/model-deprecations) but remains callable until November 30, 2026.

[Claude Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/overview) (`claude-opus-5-5`) is recognized for pricing, its June 2026 knowledge cutoff, and native web search. Standard estimates are $4 input, $0.20 cached input, and $20 output per million tokens. Model discovery supplies the account's available IDs, token limits, and effort levels.

Opus 5.5 requires adaptive thinking. Omlorix normalizes older disabled-thinking or manual-budget settings to adaptive mode and sends effort through `output_config.effort`. Auxiliary generation also accepts responses containing thinking before text. Refresh discovery and explicitly select the new model; existing saved model IDs are unchanged.
