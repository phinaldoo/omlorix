# Modal background visual proof

`before-reported.png` is the original screenshot supplied with the bug report.
The four `after-*.png` images are unedited browser captures at 1280 × 800,
with 200% CSS zoom to make the modal surface easy to inspect.

The captures use an isolated local fixture with production stylesheets in
their original page order. Note sharing is rendered by
`frontend/js/chat/notes/manager-lifecycle.js` using a fixture title and empty
share state. The login and admin captures use the original `#pendingOverlay`
and `#deleteWebsearchProviderOverlay` markup from their respective HTML pages.
No authenticated backend or actual share creation/provider deletion is needed.

The shared shell now paints one opaque `--surface-elevated` background.
Its transparent header, body, and footer show that same surface instead of
allowing page content behind the dialog to tint different sections.

Validation:

- `npm run test:frontend`: all 1,325 tests passed.
- `npm run lint:js` and `git diff --check`: passed.
- Browser inspection of all 23 static shared dialogs across chat, login,
  and admin in both modes: opaque white/light or RGB(36, 36, 40)/dark
  modal surfaces, with transparent footers sharing the parent surface.
- Note-sharing empty state and create-link form checked at 390 × 844:
  the footer remains visible, and both modes use the same opaque surface.
- All four after screenshots were visually inspected.
- PNG pixel checks at clear header/body/footer locations confirm identical
  RGB values in every after capture: (255, 255, 255) in light mode and
  (36, 36, 40) in dark mode. The reported screenshot has (249, 249, 249)
  in the header/body and (254, 254, 254) in the footer.
