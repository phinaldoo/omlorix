# LM Studio

**LM Studio** connects Omlorix to chat models served by a current LM Studio or `llmster` installation. It is intended for local or private inference and does not supply Omlorix's speech or media features.

Apply [Common Provider Settings](2_provider_settings.md) for shared fields and lifecycle rules.

## Prepare LM Studio

1. Install a current LM Studio release, download a chat model, and start its API server.
2. Enable authentication when the server is reachable by other machines.
3. Note the server root and token. From a container, `localhost` refers to the container, not the host.
4. Ensure the Omlorix application service can reach the server through DNS, firewall, and [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md).

## Configure Omlorix

Create an **LM Studio** provider with its **Base URL** and optional **API key**, select **Test Connection**, and save. Create a [model](../6_llmmodels/2_manage_llmmodels.md) from the discovered chat models, then test text before enabling vision, reasoning, or tools.

The provider's **Models** view can download, load, unload, and inspect compatible models. These actions consume the LM Studio host's disk, memory, and accelerator resources. Unloading a model does not delete it; deleting a downloaded model is a separate server-side action.

Keep **Auto-delete missing models** off when the server is intermittent or its inventory changes frequently. Use HTTPS across untrusted networks and never expose an unauthenticated LM Studio server publicly.

If discovery works but chat fails, check that the exact model is loaded, the host has sufficient memory, and the model supports the enabled capabilities. Test unload and restart behavior before treating a locally loaded model as production-ready.
