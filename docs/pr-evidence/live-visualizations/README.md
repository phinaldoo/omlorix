# Live visualization proof

These screenshots show the production Omlorix visualization renderer, local D3
bundle, design tokens, and the real backend proxy document and HTTP security
headers. The conversation around it is an isolated fixture. This does not
require an account, a running PostgreSQL server, or a live model invocation. The
state extensions below use a disposable local SQLite fixture database. The sample is an
explicitly illustrative parallel-processing model, not measured benchmark data.

Captured in the T3 collaborative Chromium browser: desktop at 1280 × 1024 CSS
pixels and narrow layout at 390 × 1080 CSS pixels. All four images were visually
inspected. No generated mockup images or screenshot edits are involved.

| Inline, light | Inline, dark |
| --- | --- |
| ![Light inline visual](light.png) | ![Dark inline visual](dark.png) |

![Expanded live visual](expanded.png)

<img src="mobile.png" alt="Narrow layout with responsive chart and controls" width="390">

## Reproduce

From the repository root, using Python with the backend dependencies installed:

```sh
python3 docs/pr-evidence/live-visualizations/serve.py
```

Open `http://127.0.0.1:8975/?verify`. In the browser console:

```js
await verifyVisualizations()
await verifyVisualizationIsolation()
await proofMount()
// Wait until the surface has data-state="ready", then:
await verifyVisualizationExport()
```

The first check drives real DOM input events through a test-only probe appended
to the fixture. It checks immediate script execution, opaque-origin isolation,
computed result changes, source view, preserved document identity and values
through expansion, inert background/focus restoration, theme synchronization,
overflow, and reset. Host controls were also exercised through browser click
and keyboard tools. Resize and rerun at a narrow viewport.

The isolation check deliberately bypasses model validation and supplies hostile
source to the production renderer. Both its fetch and self-navigation target
loopback test endpoints. The server saw zero requests; parent DOM access was
blocked, and the blocked navigation produced the host's recoverable error UI.

The export check exercises the actual toolbar exporter while suppressing the
browser's download action. Open `http://127.0.0.1:8975/__proof__/export` to inspect
the resulting standalone HTML. Its slider was verified to produce **18.9 s** at
8 workers, with the summary present, all host actions disabled, and zero remote
resource loads. Shared-chat mode was separately checked to remove follow-up and
external-data capabilities while retaining embedded interactivity.

## Validation

- `npm run test:frontend`: 1,327 passed.
- `npm run lint:js` and `npm run lint:python`: passed.
- `pytest backend/tests/tools/test_visualization.py backend/tests/files/test_canvas_html_preview_proxy.py backend/tests/chats/test_widget_blocks.py -q`: 23 passed, including all three launch-example fragments through the actual tool's validate/render dispatcher.
- `python3 dev_scripts/check_translation_keys.py`: all 11 locales match.
- Browser interaction checks: nine assertions passed at desktop and narrow widths.
- Canonical visualization metadata and literal source round-trip through chat blocks.

No live provider generation or Firefox/Safari run was performed. The model's
`validate` action checks structure/policy, not screenshots or JavaScript runtime.

## T3 launch examples: charts, heatmap, code map, and geographic map

The [launch video](https://x.com/theo/status/2107269392874782873) shows a stacked
area chart and weekly activity heatmap (around 1–6 seconds), then a code treemap
with filtering, color modes, file inspection, and folder zoom (around 12–24
seconds). These examples reproduce those interaction types in Omlorix. A
geographic map is included separately to verify geographic mapping as well.

All seven additional screenshots are unedited browser captures of the same
production renderer and backend proxy used above, with the page scrolled to the
visualization. Desktop: 1280 × 1100 CSS pixels. Narrow: 390 × 1100 CSS pixels
(captured at 2×). These are authored tool-input fixtures, not live model output.

### Stacked charts and activity heatmap

Three stacked series can switch between percentage share and absolute counts.
The week slider and heatmap pointer/keyboard selectors update exact values.
The screenshot selects week 9 (1,900 conversations); the narrow view switches
to counts and week 4 (1,200). All counts and model names are illustrative.

![Stacked area chart and weekly activity heatmap in dark mode](examples/charts-dark.png)

### Code treemap

The real Omlorix snapshot contains 1,679 Python/JavaScript files and 702,615
physical lines, including comments and whitespace, at `36df43ebc`. It covers
`backend/app`, `backend/tests`, and `frontend/js`, excluding vendor bundles.
File touches count commits among the last 100; this is not a complexity score.
Run `python3 docs/pr-evidence/live-visualizations/examples/build_code_snapshot.py`
to reproduce the pinned snapshot without changing the checkout.

Canvas renders the dense map. Folder selection, file hit-testing, ranked file
buttons, test filtering, color switching, and zoom-out all work. The narrow
view drills into rendering code with tests removed: 12 files, 7,411 lines.

![Omlorix code map showing all 1,679 source files](examples/treemap-dark.png)

![Code map drilled into frontend rendering](examples/treemap-zoom.png)

### Geographic map

The map renders 177 geographic features from embedded Natural Earth geometry
using the bundled D3/TopoJSON libraries. Country selection, keyboard activation,
zoom-in/out, world reset, and metric switching work. Germany's illustrative
values are 2,400 conversations and 180 ms. Zoom is retained during expansion.
No map tiles, CDN libraries, external requests, or additional permissions are
needed for these embedded examples.

The unmodified geometry comes from
[world-atlas 2.0.2](https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json),
redistributing [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/).
The accompanying [ISC license](examples/world-atlas.LICENSE) is retained.
The data is a fixture, not an automatic basemap included with every visual.

![Geographic map with Germany selected in light mode](examples/map-light.png)

### Narrow layouts and interaction evidence

<img src="examples/charts-mobile.png" alt="390-pixel chart with count mode and week four selected" width="390">
<img src="examples/treemap-mobile.png" alt="390-pixel code treemap drilled into rendering, with tests excluded" width="390">
<img src="examples/map-mobile.png" alt="390-pixel geographic map zoomed into Germany, with latency selected" width="390">

With the proof server running, visit each of:

- `http://127.0.0.1:8975/?example=charts&verify`
- `http://127.0.0.1:8975/?example=treemap&verify`
- `http://127.0.0.1:8975/?example=map&verify`

Run `await verifyExample()` in each page's console, starting with the authored
initial state (reload or reset first). This exercises real DOM input, click,
pointer, and keyboard events through a test-only probe in the opaque iframe.
It also checks source view, state-preserving expansion, theme redraws,
overflow, runtime status, absence of remote resource loads, and reset. The
map zoom buttons were additionally tested with the collaborative browser's
actual mouse click and Tab/Enter keyboard input.

**126 assertions passed across 1280, 390, and 320 CSS-pixel widths.** Exact
results are retained in [browser-results.json](examples/browser-results.json).
The toolbar's standalone HTML export was opened and interacted with for all
three examples, with no remote resource loads and all host actions disabled.
Use `await verifyVisualizationExport()` and then open `/__proof__/export` to
repeat that check without writing a download into the user's Downloads folder.
No new runtime permission or network access was added to make these pass.


## Saved selections and mockup controls

The `mockup` fixture declares two variants and four Tweak control types. The
runtime supplies the panel, temporary original preview, reset and follow-up
submission. These are real browser screenshots, not generated images:

![Saved design controls](state/design-desktop.png)

<img src="state/design-mobile.png" alt="Discover variant and its design controls in dark mode at 390 CSS pixels" width="390">

![Map restored after reloading](state/map-restored.png)

![Chart selections restored after reloading](state/charts-restored.png)

Open `?example=mockup&verify`, then run `await verifyStateAndDesign()` with the
panel initially closed. The 16 checks cover all control types, original preview
without writes, independent variant edits, visibility, persisted values and
cancelled/confirmed follow-ups. The confirmation/send functions in this fixture
record requests without sending a real chat message. They exercise the production
host bridge; no provider generation is claimed. Checks passed at 1280 and 390
CSS pixels; a separate 320px check verified controls remain within the frame.

The fixture's GET/PUT adapter calls the production state service and Pydantic
schemas with a fixed fixture owner and a disposable SQLite database at
`/tmp/omlorix-visual-state-proof.sqlite`. It does not exercise a logged-in app or
PostgreSQL locking. Backend tests separately verify ownership denial, revision
conflicts, source identity, size bounds, model/private separation, export/import,
clone independence and public-share filtering. PostgreSQL migration SQL was
compiled in offline mode; a live PostgreSQL migration was not run locally.

Reload checks restored Focus/Discover edits, Germany + latency + 5× map zoom,
and chart counts + week 7 + Tuesday 09:00. Native Tab/ArrowRight adjusted the
slider. Standalone export retained the edited design and working variant
navigation with authenticated host actions disabled. `Reset visualization`
clears the snapshot; `Reset design` clears styling while preserving the selected
variant. Temporary and shared previews keep local changes without writing the
owner's saved state. The classic example checks now mount temporary previews so
prior saved selections cannot change their expected initial values.
