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
- Restore selections from window.omlorix.visualization.widgetState?.privateContent
  before the initial render. On meaningful changes call
  await window.omlorix.visualization.setWidgetState({modelContent, privateContent}).
  Each call replaces the snapshot; missing fields become null. Use modelContent
  for concise selected values useful for follow-ups and privateContent for UI
  restoration. Only modelContent reaches the model; never save secrets, images,
  DOM nodes or large datasets. The combined widget/design snapshot is <=16 KiB.
  Listen to 'omlorix:statechange' (event.detail.widgetState) when useful. Saving
  never starts a turn. Catch rejected promises. Owned chats persist across reloads;
  temporary/shared previews are session-only. Reset clears saved selections.
- For mockups, use the runtime-provided Tweak helper. Do not load a package or
  draw your own settings panel. Example:
    const state = {radius: 18, accent: '#7c3aed', compact: false, size: 'Medium'};
    function render() {
      card.style.borderRadius = state.radius + 'px';
      card.style.borderColor = state.accent;
      card.style.padding = state.compact ? '12px' : '24px';
      card.style.fontSize = {Small:'14px', Medium:'16px', Large:'18px'}[state.size];
    }
    render();
    const tweak = new Tweak({container: card, onChange: render});
    tweak.addSlider(state, 'radius', {label: 'Corner radius', min: 0, max: 40, unit: 'px'});
    tweak.addColorPicker(state, 'accent', {label: 'Accent', reference: '--card-accent'});
    tweak.addToggle(state, 'compact', {label: 'Compact'});
    tweak.addSelect(state, 'size', {label: 'Size', options: ['Small', 'Medium', 'Large']});
  Give every container a stable unique id and descriptive aria-label. Use one
  Tweak per editable component (max24), at most12 controls per component and12
  select options. Slider step defaults to1. Select options can also be
  {label,value} strings. Color values are six-digit hex. Reference is optional
  code/CSS provenance without spaces. onChange must redraw deterministically,
  including original previews and reset; it must never send a message. Dispose
  removed components with tweak.dispose(). The host owns panel controls,
  persistence, original preview, reset and confirmed Apply in chat (declare
  chat_followup to enable it). Saved design values are available to follow-ups.
- To compare mockups, put direct children with unique descriptive data-variant
  names in a .viz-carousel with a stable unique id and aria-label. Show the first
  child and mark the rest hidden. The runtime provides previous/next, a named
  picker and count. It toggles hidden without replacing DOM and saves selection.
  Keep variant stages at the same responsive height. Tweak groups inside hidden
  variants are hidden too; groups outside are shared. Navigation is local and
  must not start a chat, audio or animation.

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
