# Bring Your Own Key

**Bring Your Own Key** lets you connect your own AI provider account and add personal models to Omlorix. The page appears only when your access allows it. Provider charges and account limits remain your responsibility.

## Add a provider and model

Open **Settings > Bring Your Own Key**:

1. Under **Provider Instances**, select **Add provider instance**. Choose the provider type, enter a recognizable provider name, add a Base URL when required, and enter your API key.
2. Under **BYOK Models**, select **Add model**. Choose the provider instance and remote model, wait for its tailored settings to load, review the settings and tools, then save.

If model discovery is unavailable, Omlorix offers manual model entry. Your saved models appear under **My Models** in **Select Model**. Update the provider instance when its key or **Base URL** changes, and remove models you no longer intend to use.

## Privacy and usage

Using a BYOK model sends the request, relevant conversation context, selected files, enabled tools, and model settings to the provider. Review that provider's billing, privacy, retention, and regional-processing terms.

Omlorix submits the API key once and keeps only a protected credential token in this browser tab for up to 30 days, including across reloads. Signing out or closing the tab can remove it sooner. Never place a key in a provider name, **Base URL**, prompt, or shared item.

**Usage Statistics** can track requests, tokens, estimated costs, tool use, and redacted recent errors. You can export or delete stored BYOK statistics. Cost figures are estimates; use the provider's billing page as the authority.

Deleting a model or provider instance removes its setup from Omlorix and can leave saved chats without their original selectable model. It does not delete those chats, stop provider billing elsewhere, or erase data retained by the provider.

## Browser storage and portability

Provider-instance and BYOK-model definitions are stored in the current browser profile; the raw API key is not. The protected credential token is tab-scoped. Another browser, device, or browser profile therefore needs its own BYOK setup and API key.

**Download Everything** under Data Control does not move the browser-local provider/model definitions or credential token. Server-side usage metadata can appear in the archive's export-only usage-statistics section, but self-service import does not restore it. Use **Usage Statistics > Export** when you need the dedicated BYOK statistics extract, and recreate provider/model setup manually on the destination browser.
