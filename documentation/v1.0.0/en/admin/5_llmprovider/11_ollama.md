# Ollama

**Ollama** connects Omlorix to locally hosted, private-network, or Ollama Cloud models through Ollama's native API.

Apply [Common Provider Settings](2_provider_settings.md) for shared fields and lifecycle rules.

## Connect the server

1. Install Ollama, start its server, and pull a chat-capable model.
2. Make the server reachable from the Omlorix application service. Inside a container, `localhost` is the container itself.
3. Use the Ollama server root as **Base URL**. Do not append an API operation path.
4. Leave **API key** empty for a default local server. If a gateway or Ollama Cloud requires authorization, enter the complete value it expects.
5. Select **Test Connection**, save, and create a model from an exact discovered tag.

Start with text chat. Add vision, tools, or reasoning only when the exact model reports and reliably supports it. A successful discovery test does not load the model or prove those capabilities.

Omlorix exposes only the Ollama inputs it can actually process: text, images for vision-capable models, PDFs, and text documents. Documents are converted before the Ollama request—source documents are extracted as text, while PDFs can be extracted as text or rendered into page images for a vision model. Ollama does not receive native audio or video input. The model's image and document limits apply across chat history, group context, project context, and the current message.

## Model operations

For local or private servers, the provider's **Models** view can download, load, unload, and delete Ollama models. These actions affect the Ollama host's network, disk, RAM, and accelerator use. Unloading keeps the download; deleting a tag is permanent on that server. Direct Ollama Cloud connections do not expose local model management.

Default Ollama installations are commonly unauthenticated. Keep the service on a trusted network or behind an authenticated HTTPS proxy. A local model can still send data outside your network when users enable web search, MCP, or other external tools.

Keep **Auto-delete missing models** off when the server is intermittent or tags are frequently changed. If discovery works but chat fails, verify the exact tag, available memory, server logs, and enabled model capabilities.
