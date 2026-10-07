# Visualization

**Visualization** enables models to create live inline charts, comparisons, simulations, maps, and interface previews. It needs no rendering service or model-specific integration. Enable it on the permitted models using the shared [Tool Rollout Checklist](0_tool_rollout.md).

## Generation and runtime

The provider-neutral `create_visualization` tool accepts `title`, `content`, optional `summary`, `mode` (`normal` or `wide`), and optional host-action capabilities. `action=render` is the default and emits a durable chat widget. `action=validate` checks the same structure, size, and authoring rules without displaying a widget. Validation does not execute JavaScript or produce screenshots. Errors can be repaired before rendering.

Enabled models receive an authoring guide covering when to create a visual, theme tokens, available controls and libraries, responsive charts, accessibility, and consent-gated actions. Fragments are limited to 1 MiB of UTF-8. D3, TopoJSON, and Lucide are supplied from local versioned assets when referenced. A long transcript mounts visuals only as they approach the viewport; generation never starts a server-side browser.

The trusted `/api/v1/files/canvas/visualization-preview-proxy` endpoint has an HTTP Content Security Policy that disallows network connections, frame navigation, workers, forms, and external resources. It embeds authored content in an opaque sandbox with only `allow-scripts`. No application storage or authenticated file hydration is exposed. The proxy is public to support shared chats, contains no user data, and permits framing only by the application origin. Canvas's separate, consent-based HTML preview retains its existing permissions.

Local scripts run immediately unless `capabilities.scripts=false`. Follow-up messages, public external-data requests, and generated downloads remain separately gated by capability and user confirmation. Shared chats disable authenticated host actions. Host toolbar actions are outside the sandbox.

## Compatibility and checks

Existing visualization blocks use the new renderer. Version-2 source, summary, mode, and capability metadata stay in the existing chat block format and follow chat backup, import, export, and sharing; apply migration `visualization_state_20261007` to add the nullable `chat_messages.visualization_states` JSON column. Existing messages need no backfill. Snapshots are stored separately from generated HTML and retained by chat/user-data exports, imports, branching, duplication, and database backups. Standalone HTML includes local runtime assets and the current snapshot, and disables host actions.

## Saved state and mockup authoring

`window.omlorix.visualization.widgetState` provides the restored snapshot. `setWidgetState({modelContent, privateContent})` replaces it; omitted fields become null. Listen for `omlorix:statechange` with `event.detail.widgetState`. `modelContent` is a concise selection summary available to follow-ups; `privateContent` is for restoring UI values and is never added to model context. The combined widget/design JSON limit is 16 KiB, with at most 32 saved widgets and 256 KiB per message. The latest eight snapshots, bounded to 4 KiB, supplement the next user turn across providers. Snapshots that exceed this context allowance are omitted; keep model-visible summaries concise. Never put secrets or large datasets into snapshots.

The owner-only GET/PUT `/api/v1/chats/messages/{message_id}/visualizations/{tool_call_id}/state` endpoints enforce chat ownership, current project access, widget identity and source identity. PUT requires the revision returned by GET; conflicting tabs receive 409. Saves are coalesced, flushed before follow-ups and on page hiding, and queued until a live message has a server ID. Changes update only the bounded JSON column. Shared and temporary previews use local session state and do not write the owner's snapshot. Saving does not initiate generation. Failed saves remain visible and can be retried.

Models declare editable components through `new Tweak({container, onChange})`, with stable container IDs and descriptive `aria-label`s. `addSlider`, `addColorPicker`, `addToggle`, and `addSelect` bind properties on a local state object. The injected Omlorix runtime supplies the accessible panel, original preview, reset and capability-gated, confirmed follow-up. It is not an external Tweak.js dependency. Groups inside inactive variants are hidden; groups outside are shared. Callback functions should only redraw locally. Up to 24 components, 12 controls each and 12 select options are supported; use `.dispose()` for removed components.

A `.viz-carousel` with a stable ID and direct children carrying unique `data-variant` names gets previous/next buttons, a named picker and a counter. Variant switching toggles `hidden` without rebuilding the DOM. It persists independently of widget state. Exported files include the same design and variant controls and start with the downloaded snapshot; further changes in the file remain local.

Test local controls, source view, reset, HTML download, expansion/collapse, theme changes, a narrow viewport, keyboard focus, text alternatives, and malformed scripts. Verify optional host actions individually and test shared chats. Models should use accessible labels, sufficient contrast, honest units/scales, and text or table equivalents. Validation is an authoring aid; the browser sandbox enforces isolation independently, including for imported content.

Use [Canvas](16_canvas.md) for durable editable documents and [Image Generation](6_image_generation.md) for raster artwork.

## Study activities and retired tools

Quizzes and flashcards now use `create_visualization`. The built-in authoring guide covers answer submission, scoring, explanations, retrying missed questions, card reveal and recall ratings, bounded review queues, keyboard access, and saved progress. No separate skill installation or study service is required. Interfaces are generated for each request; they are self-study aids, not a grading system or cross-chat spaced-repetition scheduler.

Saved/imported model selections and BYOK allowlists named `quiz`, `create_quiz`, `flashcards`, or `create_flashcards` resolve to Visualization and are deduplicated. Admin model editors show the replacement. Old rate-limit tool keys also match Visualization, retaining existing policy precedence; review their budgets because Visualization supports more activities. Compatibility is applied when reading settings, so backups containing old names continue to work without a data rewrite.

The retired names are no longer advertised or dispatched. Historical structured study widgets remain readable as expandable questions/cards with answers and explanations. Public sharing keeps its existing reviewed static projections. Chat content and backup formats remain unchanged.
