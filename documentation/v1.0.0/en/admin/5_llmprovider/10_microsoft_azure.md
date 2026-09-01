# Microsoft Azure

Use **Microsoft Azure** for chat deployments in an Azure OpenAI resource.

Apply [Common Provider Settings](2_provider_settings.md) for shared credential and lifecycle rules.

## Before you begin

Prepare:

- an Azure OpenAI resource and API key;
- a deployment that supports the Responses API;
- the resource **Azure endpoint**;
- the exact deployment name users will call;
- quota, regional processing, firewall, and private-network access where applicable.

## Configure

1. Create a **Microsoft Azure** provider.
2. Enter the **API key** and **Azure endpoint**. Set **API version** only when the deployment requires it.
3. Add **Custom headers** only for an approved gateway.
4. Select **Test Connection** and save.
5. Create a model using the exact Azure deployment name. Manual entry may be necessary when Azure does not return a usable model list.
6. Test generation and every enabled file, reasoning, or tool capability.

The deployment name can differ from the underlying model, so Omlorix cannot always infer its capabilities. Enable only settings you have verified. Keep **Auto-delete missing models** off for manual or incomplete Azure model lists.

For Private Link, test resolution and TLS from the Omlorix service network. Use Azure Cost Management as the billing authority and review regional data handling before rollout. A deployment can remain listed while its quota is exhausted, so include one real generation in health checks.
