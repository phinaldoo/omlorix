# Visualization

**Visualization** enables models to create live inline charts, comparisons, simulations, maps, and interface previews. It needs no rendering service or model-specific integration. Enable it on the permitted models using the shared [Tool Rollout Checklist](0_tool_rollout.md).

## Generation and runtime

The provider-neutral `create_visualization` tool accepts `title`, `content`, optional `summary`, `mode` (`normal` or `wide`), and optional host-action capabilities. `action=render` is the default and emits a durable chat widget. `action=validate` checks the same structure, size, and authoring rules without displaying a widget. Validation does not execute JavaScript or produce screenshots. Errors can be repaired before rendering.

Enabled models receive an authoring guide covering when to create a visual, theme tokens, available controls and libraries, responsive charts, accessibility, and consent-gated actions. Fragments are limited to 1 MiB of UTF-8. D3, TopoJSON, and Lucide are supplied from local versioned assets when referenced. A long transcript mounts visuals only as they approach the viewport; generation never starts a server-side browser.

The trusted `/api/v1/files/canvas/visualization-preview-proxy` endpoint has an HTTP Content Security Policy that disallows network connections, frame navigation, workers, forms, and external resources. It embeds authored content in an opaque sandbox with only `allow-scripts`. No application storage or authenticated file hydration is exposed. The proxy is public to support shared chats, contains no user data, and permits framing only by the application origin. Canvas's separate, consent-based HTML preview retains its existing permissions.

Local scripts run immediately unless `capabilities.scripts=false`. Follow-up messages, public external-data requests, and generated downloads remain separately gated by capability and user confirmation. Shared chats disable authenticated host actions. Host toolbar actions are outside the sandbox.

## Compatibility and checks

Existing visualization blocks use the new renderer. Version-2 source, summary, mode, and capability metadata stay in the existing chat block format and follow chat backup, import, export, and sharing; no database migration is needed. Interaction state is ephemeral. Exported standalone HTML includes local runtime assets and the original values, and disables host actions.

Test local controls, source view, reset, HTML download, expansion/collapse, theme changes, a narrow viewport, keyboard focus, text alternatives, and malformed scripts. Verify optional host actions individually and test shared chats. Models should use accessible labels, sufficient contrast, honest units/scales, and text or table equivalents. Validation is an authoring aid; the browser sandbox enforces isolation independently, including for imported content.

Use [Canvas](16_canvas.md) for durable editable documents and [Image Generation](6_image_generation.md) for raster artwork.
