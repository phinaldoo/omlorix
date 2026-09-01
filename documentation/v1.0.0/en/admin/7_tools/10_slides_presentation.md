# Slide Presentation

**Slide Presentation** lets a model turn one owned UTF-8 Markdown brief into an editable HTML presentation with previews and downloads. The brief must contain all required content, requirements, and source notes. Rendering requires a healthy Slides [Service Connection](14_service_connections.md).

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then validate both generation and rendering.

## Configure

1. Deploy a compatible slide-rendering service and add it under **Admin Settings > Service Connections** with **Slides** enabled.
2. Open **Admin Settings > Tools > Slide Presentation** and select a suitable presentation model and the visible generation, rendering, and file settings.
3. Save, then select **Slide Presentation** on the chat models that may start the workflow.
4. Have a pilot user create or upload the complete Markdown brief, generate a small deck from that file, edit it in the browser, rerender it, and download each offered format. Up to 20 owned image files can be supplied as presentation assets.

The presentation model and the chat model have different roles: the chat model gathers the request, while the presentation model produces the deck. Both must remain available and within quota.

## Files and lifecycle

Presentations, previews, source, images, and downloads are stored as user files and count toward storage limits. Attached images must be accessible to the user and suitable for use. Test deletion and retention as well as successful generation.

User and account archives include Slide Presentation records and the renderer artifacts available at export time. Import remaps known file IDs and writes included artifacts to destination storage, but marks imported render derivatives stale. Rerender after restore, then verify each brief, asset, preview, and downloadable output.

Edits can conflict when the model and user change the same presentation at once. The last valid saved revision remains the recovery point; reload before retrying a rejected save.

## Security and operations

- Treat generated HTML and imported images as untrusted content. Keep the rendering service isolated and authenticated.
- Give the renderer only the storage and network access it needs.
- Use HTTPS across untrusted networks and rotate Service Connection credentials deliberately.
- Review copyright, sensitive-data, template, and branding requirements before rollout.
- Rendering and model calls can be slow and expensive; set budgets, rate limits, timeouts, and monitoring.

If generation produces text instead of a deck, verify the tool assignment and group access. If the source exists but previews or downloads fail, check **Service Connections**, renderer health, storage quota, and renderer logs. A healthy connection only proves its health check; run a real render after every service change.
