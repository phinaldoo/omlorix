# Slide Presentation

**Slide Presentation** lets a model turn one owned UTF-8 Markdown brief into an editable HTML presentation with previews and downloads. The brief must contain all required content, requirements, and source notes. Rendering requires a healthy Slides [Service Connection](14_service_connections.md).

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then validate both generation and rendering.

## Configure

1. Deploy a compatible slide-rendering service and add it under **Admin Settings > Service Connections** with **Slides** enabled.
2. Open **Admin Settings > Tools > Slide Presentation** and select a presentation model that supports image input and tool calling.
3. Save, then select **Slide Presentation** on the chat models that may start the workflow.
4. Have a pilot user create or upload the complete Markdown brief, generate a small deck from that file, edit it in the browser, rerender it, and download each offered format. Up to 20 owned image files can be supplied as presentation assets.

The presentation model and the chat model have different roles: the chat model gathers the request, while the presentation model produces the deck. Both must remain available and within quota.

The presentation model runs as a purpose-specific subagent through the shared subagent/provider runtime. Its tool set is explicitly **update_presentation** and **Code Execution**, independently of its saved chat tools. Code Execution is always offered but never required: the specialist can use it to calculate values or create image assets when useful. A healthy Code Execution service is needed only when the specialist chooses to execute code; normal execution-service network, package, quota and rate-limit policies still apply. The public Subagent tool and its target picker do not need to be enabled to generate presentations.

One continuing conversation retains the complete brief, tool results and visual feedback. Each run permits **12 total tool calls** and **4 write/render attempts**, including failed attempts; source reads consume a tool call but no render. Every tool result reports both remaining budgets. Once all tool calls are spent, the model receives the last result for a final response with tools disabled. Code Execution can be skipped entirely. Updates use the existing Canvas create/read/exact-edit contract and accept only this deck, approved input images and files generated during the run. Rendered contact sheets are temporary, owner-scoped model attachments, removed when the run ends; they are not added to Workspace Files or account archives.

Each valid candidate is rendered before publication. Canonical HTML, revision metadata, the presentation index and the new artifact pointer are committed together using the Canvas transaction hook. A failed render or publication leaves the last successful revision available. Rendering still produces the renderer's existing PPTX-and-PNG bundle on each pass; there is no new renderer API requirement. The existing Rendering Worker can run the workflow, and parent cancellation propagates to the specialist.

## Files and lifecycle

**HTML source** is available in both presentation download menus. It downloads the canonical HTML file through the existing authenticated, audited file-download endpoint, preserving the saved source and embedded assets. Editor downloads save pending edits first; HTML does not require a successful Slides render. PPTX, PDF, and image exports continue to wait for updated derivatives. The source download does not bundle Omlorix’s runtime player or capture live viewer state.

The fullscreen slideshow creates a short-lived HTTP frame from the owned, saved HTML source. Scripts run only in an opaque-origin sandbox without same-origin privileges. The HTTP document has its own CSP, including on browsers that inherit the parent CSP for srcdoc. It does not wait for the Slides service or download slide PNGs for playback. The service remains necessary for generation, visual review, rendered sidebar images, and export derivatives. Playback documents use the configured file/object storage and support the full 64 MiB source limit plus runtime markup. Only small storage references count against the shared widget-frame cache quotas in Redis (bounded local memory for development). Frame URLs expire after five minutes; the loaded frame remains usable after its URL expires. Existing file maintenance removes expired playback documents after a 15-minute transfer grace period. Apply migration `playback_documents_20260905` when upgrading.

Interactive HTML, inline code, network declarations live in the existing source file and therefore follow normal account import/export. No new tables are required. Playback frames and per-viewer quiz/control state are transient and are not archived.

Presentations, previews, source, images, and downloads are stored as user files and count toward storage limits. Attached images must be accessible to the user and suitable for use. Test deletion and retention as well as successful generation.

User and account archives include Slide Presentation records and the renderer artifacts available at export time. Import remaps known file IDs and writes included artifacts to destination storage, but marks imported render derivatives stale. Rerender after restore, then verify each brief, asset, preview, and downloadable output.

Edits can conflict when the model and user change the same presentation at once. The last valid saved revision remains the recovery point; reload before retrying a rejected save.

## Security and operations

- Playback network access is restricted to declared public HTTPS origins; app-origin access, local host literals, credentials in URLs, wildcards, external scripts and privileged browser actions are disallowed. Public APIs still need CORS, and embeds still obey the destination’s framing policy.
- The renderer receives executable HTML and the presentation runtime. It must run JavaScript and allow declared internet content. For asynchronous initialization, configure capture to await `window.omlorixPresentationRenderReady` or `html[data-omlorix-render-ready="true"]`; readiness rejects on task failure or after 30 seconds. PDF, PPTX, images and visual review use the live rendered document. No static snapshot or offline alternative is generated.
- Treat generated HTML and imported images as untrusted content. Keep the rendering service isolated and authenticated.
- Give the renderer only the storage and network access it needs.
- Use HTTPS across untrusted networks and rotate Service Connection credentials deliberately.
- Review copyright, sensitive-data, template, and branding requirements before rollout.
- Rendering and model calls can be slow and expensive; set budgets, rate limits, timeouts, and monitoring.

If generation produces text instead of a deck, verify the tool assignment and group access. If the source exists but previews or downloads fail, check **Service Connections**, renderer health, storage quota, and renderer logs. A healthy connection only proves its health check; run a real render after every service change.

See the [interactive presentation runtime contract](../../architecture/2_interactive_presentations.md) for authoring, lifecycle, export and API details.
