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
