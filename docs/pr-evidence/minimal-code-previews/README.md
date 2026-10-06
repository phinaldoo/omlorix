# Compact code and preview blocks

These screenshots show the production Markdown parser, code-block controls,
Mermaid renderer, and styles used by `frontend/index.html`, in an isolated
fixture. HTML uses the production preview runtime and the backend's exact
proxy document and CSP headers. No authenticated backend or LLM is needed.

- [Light desktop](light.png): 1120 × 960 CSS pixels.
- [Dark desktop](dark.png): 1120 × 960 CSS pixels.
- [Mobile layout](mobile.png): 375 × 900 CSS pixels.
- [Fullscreen Mermaid](fullscreen.png): 1120 × 960 CSS pixels, vertical flowchart.

Screenshots were captured with T3's collaborative Chromium browser and visually
inspected. They are renderer evidence, not screenshots of a live account.

## Reproduce

From the repository root, with Node.js and Python 3 available:

```sh
node docs/pr-evidence/minimal-code-previews/serve.cjs
```

Open `http://localhost:8765`. Use `?mode=dark` for dark mode, `?lang=ar` for
translated RTL controls, or `?layout=vertical` for the fullscreen example.
Click the expand icon to open the fullscreen view. Resize the browser to
375 pixels wide for the mobile screenshot.

Run `await runPreviewChecks()` in the browser console. The checks exercise
production handlers for proportional zoom, cursor anchoring, ordinary wheel
scrolling, keyboard panning/reset, touch drag/pinch/cancellation, tab state,
fullscreen focus trapping/restoration, fitting a 40-edge diagram below 25%,
resize, disposal, and overlapping asynchronous mounts. Pointer events are
synthetic; only native pointer capture is stubbed during the touch check.
These checks do not replace testing gestures on physical mobile devices or Safari.

The 18 checks passed at desktop and mobile widths and with an RTL document.
Translated Arabic control labels and diagram bounds were also inspected.
`npm run test:frontend` passes 1,327 tests; `npm run lint:js` passes.

## Diagram controls

Drag with a mouse or one finger to pan; pinch with two fingers or use
Ctrl/⌘ + wheel to zoom at the pointer. Ordinary wheel scrolling continues
through the conversation. With the diagram focused, use +/− to zoom, arrow
keys to pan (Shift for larger steps), and Home or 0 to fit. The reset button
also fits the diagram. Zoom ranges from the smaller of 10% or the fit scale
to 800%, so very large diagrams remain fully reachable. Resizing refits an
untouched diagram and preserves the center and scale of a manually navigated
one. Source/preview tab switching preserves the inline viewport; fullscreen
opens fitted independently and restores focus on close.
