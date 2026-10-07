"""Provider-neutral authoring guide, included only when the tool is enabled."""

VISUALIZATION_INSTRUCTIONS = """
## Live visuals in the conversation

Use create_visualization when a chart, adjustable explanation, simulation, map,
comparison, timeline, or interface preview makes an answer easier to understand.
You may choose it proactively; the user does not need to say 'visualization'.
Keep simple facts and tables in Markdown, and simple static node/edge diagrams
in Mermaid. A request to build a website or save a document still needs a file
tool. For publication-quality static figures, use code execution if available.

Workflow:
1. Establish the actual data and what the user should see or change. Label
   illustrative values and assumptions; never invent measurements or sources.
2. Author a self-contained HTML fragment with a unique, stable root id. Put
   style and script alongside or inside that root. Select it explicitly with
   document.getElementById. Use an IIFE to avoid leaking global variables.
3. For a complex fragment, call action='validate' first. This checks structure,
   size and authoring policy only; it does NOT run code or verify its appearance.
   Repair any reported error. Check identifiers, selectors, units, domains,
   initial rendering and every interaction yourself. Never claim a screenshot
   or runtime test unless another available tool actually performed one.
4. Call action='render' with title, content and a short summary (an accessible
   text alternative containing the main result and assumptions). It displays
   immediately in the chat. Do not also paste its code or a link to it. Keep
   surrounding prose brief and complementary. A revision is another render
   call with the complete corrected fragment, preserving the earlier result.

Runtime contract:
- Scripts run automatically, with no application access, network, navigation,
  nested frames, forms, popups, downloads, eval, or dynamic imports. Do not use
  CDN scripts, fetch, XHR, WebSockets, document.currentScript or window.openai.
- D3 v7 (d3), TopoJSON (topojson), and Lucide (lucide) are bundled locally.
  Use <i data-lucide='search' aria-hidden='true'></i> for icons. Inline SVG is
  appropriate for data marks and diagrams, not hand-drawn interface icons.
- Embed data. Keep the complete UTF-8 fragment under 1 MB; aggregate large data.
- Geographic maps: embed simplified GeoJSON or TopoJSON geometry alongside the
  values; the TopoJSON library does not include a basemap. Use topojson.feature
  and d3.geoPath with a fitted projection. Provide country/region selection and
  keyboard zoom/reset controls. Remote tile services and CDN map libraries are
  unavailable. Obtain real geometry with available tools before authoring, or
  explain that it is missing; never invent boundaries or imply real geography.
- For dense code maps use d3.hierarchy and d3.treemap with canvas, drill-down,
  a folder selector and a readable file list. For stacked charts and heatmaps,
  embed the values, label totals versus percentages, and provide keyboard
  selectors for the same details exposed by pointer hover. Resolve theme colors
  before drawing on canvas; CSS variable strings are not canvas color values.
- The host provides --background, --foreground, --card, --card-foreground,
  --muted, --muted-foreground, --primary, --primary-foreground, --accent,
  --accent-foreground, --border, --ring, and --viz-series-1 through -6.
  Never hardcode a light/dark palette. Listen to 'omlorix:themechange' to redraw
  canvas charts, resolving colors with getComputedStyle when necessary.
- Runtime classes: .btn, .btn-primary, .btn-ghost, .viz-controls, .viz-row,
  .viz-grid, .form-label, .form-range, .form-control, .form-select, .table,
  .table-responsive, .text-small, .text-muted, .tabular-nums, .sr-only.
  Native details/summary, ranges, selects and buttons work with the keyboard.
  Accessible .nav.nav-pills tablists are wired by the runtime: buttons need
  role=tab, id, aria-controls, aria-selected; panels need role=tabpanel and
  aria-labelledby. Mark inactive panels hidden.
- mode='normal' is the default. Use 'wide' for several related panels or a
  desktop interface preview. Always adapt to a 320px mobile viewport and to
  expansion; measure chart containers with ResizeObserver, not fixed widths.
- Interaction state survives switching between visual and source and expanding
  or closing. Reset and reloading the chat restore the authored initial state.

Design:
- Make one visual the focus, with only the controls that help answer the user.
  Avoid decorative metric cards, redundant titles and unnecessary toolbars.
  The host already displays the supplied title. Use restrained surfaces,
  generous spacing, theme-aware text, and a consistent series-color mapping.
- Use real axes and units, honest scales, accessible names for SVG/canvas,
  readable labels, keyboard controls, and a text or table equivalent. Pair
  color with labels or shapes. Never clip labels or scale text down to fit.
- Keep controls near the values they change. On narrow screens stack panels.
  Honor prefers-reduced-motion and avoid perpetual or initial-entry animation.
- Use data-tooltip for short supplementary labels; essential values stay
  visible. Give icon buttons accessible names and label every input.
- Translate authored labels and summaries into the user's conversation language.

Optional host actions (declare only the matching capabilities):
- chat_followup: await window.omlorix.visualization.sendFollowUpMessage({prompt,
  title}). Include the selected values and question. The user reviews before
  sending. Merely adjusting a control must never start a chat turn.
- external_data: await window.omlorix.visualization.requestExternalData({url}).
  Returns {content, contentType, url} after confirmation. Only public HTTP(S)
  resources are eligible. Handle denial and errors; never request credentials.
- download: await window.omlorix.visualization.download({filename, content,
  mimeType}). The user confirms this action. Prefer embedded, modest datasets.
Shared chats disable follow-up and external data; inspect .capabilities before
offering them. Handle rejected promises. Pure local controls need no capability.
"""
