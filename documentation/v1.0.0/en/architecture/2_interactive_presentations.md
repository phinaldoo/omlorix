# Interactive presentations

The saved HTML is the source of truth. A deck is limited to 50 fixed 1920 × 1080 `section.slide` elements, one stylesheet, sequential one-based `data-slide-index` values and meaningful `data-slide-title` values. Set `data-omlorix-interactive="1"` on `html` to retain inline JavaScript and interactive controls. Unmarked legacy sources remain static.

## Generation and review

Presentation review workspaces hold a POSIX filesystem lease for the entire specialist run. Temporary-file maintenance does not descend into leased directories, even when their images exceed the normal materialized-file age limit. Fresh empty directories also receive the normal age grace period, protecting workspace creation before lease acquisition. A process crash releases the lease automatically; abandoned files and directories then follow the existing cleanup age policy. API, rendering and maintenance workers must run the same backend version and share the local temporary storage with filesystem-lock support.

Unexpected scoped-tool exceptions produce a safe failed receipt with remaining budgets and stop the specialist before another provider request. If a rendered revision exists it is retained with incomplete review; otherwise the non-retryable failure propagates through the durable rendering worker to the parent tool. Correctable validation errors remain eligible for correction within the existing budgets. Scoped error receipts are recorded as failed tool statistics, not successful calls; their metadata follows the existing statistics export path without a migration.

`slide_presentation` dispatches the configured administrator-managed model through the shared subagent runtime with a task-scoped `SubagentSession`. The session exposes only `update_presentation` and optional-use `code_execution`; it does not inherit the parent chat's tools or expose recursive delegation. The Markdown brief and approved images initialize a single continuing conversation. Provider-native tool responses and reasoning continuation remain under the shared generation engine. Scoped runs preserve the complete brief and tool history. Historical review images are retained for cache-prefix reuse while the request fits the configured context budget. Under context pressure, the engine prunes superseded review images and retries local budgeting once, preserving tool pairs, text, reasoning signatures, original assets and the latest screenshots. Removed images stay removed for the rest of the run, even if more space becomes available. If the required context still does not fit, the run stops instead of silently dropping its requirements. If generation or review is interrupted by an error, the result explicitly reports `review_status: incomplete` when returning the last successfully rendered deck.

`update_presentation` reuses Canvas argument validation, bounded source reads, atomic snippet edits and revision-aware persistence. Writes render a candidate first; Canvas's `before_commit` hook publishes its source, index, artifact pointer and audit intents in one database transaction. Staging files and unpublished PPTX files are removed on failure. The existing renderer protocol returns PPTX and PNGs on each pass for exports and the specialist's internal visual review, not for the user-facing sidebar. The final `complete` event retains its existing artifact contract.

### Live interactive sidebar

The sidebar displays one complete interactive HTML document, using the same origin-isolated playback runtime as Present. Navigating slides does not reload it, so controls, quizzes and deck-local state survive navigation. Its outline uses nonexecuting HTML thumbnails, not rendered slide images. Reopening a deck reads its current source; closing the editor refreshes saved HTML without waiting for rendering or submitting a render job. PDF/PPTX/image downloads continue using the existing export endpoints and rendered artifacts.

The specialist forwards coalesced `html_snapshot` events from streamed `update_presentation` content arguments when the provider exposes argument deltas. Partial exact edits are not executed: an edit emits the complete, asset-embedded candidate after validation and before the external renderer starts. Publication is still conditional on a successful render. `revision_ready` and final completion reconcile the sidebar to the saved source; if polishing fails, the last published revision replaces the provisional candidate.

`POST /api/v1/presentations/preview` accepts authenticated, bounded unsaved HTML and a slide index. It repairs incomplete markup, withholds unfinished scripts/styles, applies the existing presentation sanitizer, validates the slide contract, and uses the ordinary expiring, quota-accounted frame store and cleanup ledger. It accepts no file IDs, resolves no other users' assets and does not save a canonical deck. Scripts only run inside an opaque-origin frame with both HTTP and iframe sandboxing; external access remains limited by declared public origins. Incomplete invalid drafts leave the last good preview visible until a later snapshot is usable.

The browser coalesces updates for one second, keeps at most one update in flight plus the newest queued snapshot, and swaps frames only after a source/channel/origin-validated readiness message. Switching decks or resetting the chat aborts pending updates and invalidates late responses. HTML revisions reload their isolated document (resetting interactive state), while ordinary navigation keeps it mounted. Hiding the sidebar pauses managed runtime activity; arbitrary authored JavaScript is still responsible for following the documented lifecycle APIs. No raster image requests are made for sidebar display.

Before a raster/PPTX download, the client checks the saved revision and requests rendering only if that revision lacks current artifacts. A render failure blocks that download rather than serving an older deck. HTML downloads do not wait for this work. To run the isolated browser regression, install Playwright Chromium and run `pytest backend/tests/tools/test_slide_presentation_preview_browser.py`; it uses synthetic intercepted APIs, not a running app or live model.

The backend enforces 12 tool calls and 4 write/render attempts per run, reports the remaining counts after every result, and disables tools for the final model response. Four slides per contact sheet provide bounded visual feedback. The sheets resolve through the normal provider attachment encoders, but only inside the owner-bound subagent scope; their temporary files are removed on exit and are covered by materialized-file maintenance after process interruption. Model-created image assets use ordinary owned files and the existing `omlorix-file://` embedding, quota, retention and account import/export paths. No new persistent settings or tables are introduced.

### Cache stability and benchmark

At call-budget exhaustion, tool definitions and their order remain unchanged: OpenAI Responses, Chat Completions and OpenRouter use `tool_choice: "none"`; Anthropic uses `tool_choice: {"type": "none"}`; Gemini keeps `GenerateContentConfig.tools` and sets function-calling mode to `NONE`. The execution guard independently rejects any further calls, and only one final provider request is allowed. Native Ollama still removes definitions because its chat API has no tool-choice control. Keeping schemas avoids one source of invalidation, not every provider-specific cache miss: Anthropic documents that changing tool choice invalidates the messages-cache layer while retaining the tools/system layers. See [OpenAI caching guidance](https://developers.openai.com/api/docs/guides/prompt-caching#how-to-optimize-prompt-caching), [Anthropic cache behavior](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching), [Gemini calling modes](https://ai.google.dev/gemini-api/docs/function-calling#function_calling_modes) and [Ollama chat parameters](https://docs.ollama.com/api/chat).

Run the repeatable offline comparison from the repository root with the backend Python dependencies installed:

```sh
python dev_scripts/benchmark_presentation_cache.py --slides 16 --renders 4 --window 262144
```

The fixture uses the current presentation instructions and Canvas parameter schema, synthetic initial HTML, small subsequent edits, and one image per four slides. It compares the previous behavior (prune + remove schemas), pruning with stable schemas, and retention with context-pressure fallback. It deliberately exhausts the call budget on the last pass to test that boundary; production still allows 12 calls, and ordinary completion before exhaustion does not disable tools.

Offline results on 2026-09-06 (16 slides, four renders, five requests):

| Policy | Total estimated input units | Reusable prefix units | Reusable share | Peak context estimate |
| --- | ---: | ---: | ---: | ---: |
| Prune + remove schemas | 300,610 | 96,671 | 32.2% | 71,130 |
| Prune + stable schemas | 305,939 | 134,677 | 44.0% | 71,826 |
| Retain + stable schemas | 502,643 | 332,449 | 66.1% | 170,178 |

With a 120,000 input window, retention falls back once: 53.9% reusable share and a 104,610 peak context estimate. With 50 slides and a 262,144 window, the conservative image estimates require pruning at each later render; retention then matches the stable-schema pruning result. Both policies are identical before any image is superseded.

These are **not measured API tokens, cache hit rates, costs or latency**. Units use the application's conservative UTF-8/media estimator, and reuse counts only identical complete input-prefix blocks from earlier requests, with a cold first request. The fixture excludes optional Code Execution, provider-generated output caching, cache granularity/expiry/routing, reasoning and provider serialization. Retention improves prefix continuity but increases input volume (64.3% versus stable-schema pruning in this small-edit fixture), so a higher reusable share does not establish a billing saving. Before making cost or latency claims, compare matched live runs on the configured presentation model using input/output/cache-read/cache-write usage and elapsed time; the existing aggregate statistics alone do not identify individual cache-invalidating turns.

## Execution boundary

`POST /api/v1/presentations/{id}/playback` authenticates the caller, checks source ownership, builds a source snapshot and records a playback audit event. Its response contains a short-lived frame URL, channel ID and slide count. The frame uses the existing quota-accounted widget frame store and HTTP response headers. The parent keeps one frame mounted during playback and accepts only a small set of messages from that exact frame, its opaque origin and its channel. Messages can navigate the current deck, update its counter, reveal controls, return keyboard focus, or close/toggle the viewer. There is no bridge to arbitrary app APIs.

Frame URLs expire after five minutes; already loaded decks continue running. Playback documents use the configured file storage adapter (local, S3, GCS, Azure or WebDAV) under the reserved `_playback_frames/` prefix. Each opening stores an immutable copy of the validated source plus runtime markup, supporting the full 64 MiB source limit and its embedded assets. Redis (or the bounded development fallback) retains only the storage reference, owner hash, CSP and expiry. Existing widget token-count and cache-byte quotas still apply to these small records; ordinary widget HTML retains its existing limits. The frame endpoint materializes the document and serves it in file chunks with the same isolation headers, removing the materialized copy after the response.

Migration `playback_documents_20260905` adds a content-free cleanup ledger. The existing temporary-file maintenance worker removes stored documents after expiry with a 15-minute transfer grace period, including documents whose tokens were evicted or lost during a Redis restart. Failed deletions are retried. Uploads are registered before writing, so interrupted uploads are also cleaned up (after one hour). Temporary materialized copies use the existing materialized-file cleanup policy if a response is interrupted. These playback derivatives and their ledger are operational state, excluded from user/admin account data exports; canonical presentation sources and exports retain their existing backup/import support. Local storage must be shared between API and maintenance processes, as for other locally stored application files.

Both the iframe attribute and HTTP CSP apply `sandbox allow-scripts`, without `allow-same-origin`. The document cannot access the authenticated application DOM or storage. Scripts and styles are inline; external script imports, workers, forms, popups, top navigation and device privileges are unavailable. Stored HTML retains a script-disabled CSP. Executable documents replace it with the declared network policy and install the shared presentation runtime.

## Runtime API

Code initializes with `OmlorixPresentation.ready.then(api => { ... })`.

| API | Behavior |
| --- | --- |
| `api.index`, `api.count` | Zero-based current index and slide count. |
| `api.goTo(index)`, `api.next()`, `api.previous()` | Navigate without recreating the deck. |
| `api.state` | Shared, session-only object. Reopening resets it. |
| `api.signal` | AbortSignal for work on the currently active slide. |
| `api.interval(fn, ms, signal)` | Managed interval; returns cancellation function. |
| `api.animate(fn, signal)` | Managed animation loop; returns cancellation function; one frame with reduced motion. |
| `api.reducedMotion` | Current reduced-motion preference. |
| `api.fetchJSON(url, {signal, timeout})` | Public CORS GET with credentials omitted, redirects rejected, a default 8-second timeout (maximum 30 seconds), and a 2 MiB response limit. |

`omlorix:slide-enter` bubbles from the active slide. Its detail includes `index`, `previousIndex`, `slide`, `signal`, `state`, and `reducedMotion`. Start temporary work there and attach it to the signal. `omlorix:slide-leave` reports `index` and `nextIndex`. A cancelable document event, `omlorix:before-slide-change`, allows branching logic to prevent a navigation. `omlorix:motion-change` reports preference changes. Hiding the page aborts managed work; returning enters the active slide again. Bind permanent quiz/control listeners once in `ready`, or attach temporary listeners with the enter signal.

Inactive slides are inert and hidden. Their media and animations are paused after transitions; embeds are unloaded on leave and reload on entry. Use the managed APIs for timers/animation loops—arbitrary user JavaScript cannot be automatically rewritten into lifecycle-aware code. State remains local to the current browser session. Shared polling, audience voting or persistence requires an explicitly supplied external service and an appropriate data-handling design.

## Transitions and controls

There is no built-in or default transition. Without authored motion, navigation is immediate. CSS authors declare `data-transition-duration="650"` on the incoming slide or `html`, then animate `.omlorix-entering`, `.omlorix-leaving`, and `.omlorix-active` in the deck stylesheet.

For JavaScript, a document `omlorix:transition` event provides `incoming`, `outgoing`, `index`, `previousIndex`, `direction`, `initial`, `reducedMotion`, `signal`, and `waitUntil(promise)`. Register animation completion promises synchronously with `waitUntil`; both slides remain mounted until they settle. Cancel authored Web Animations when the signal aborts. Navigation interruptions and reduced-motion changes end the previous transition. Initial entry and reduced motion complete immediately; a ten-second ceiling prevents broken code from retaining an outgoing slide indefinitely. The runtime supplies lifecycle coordination, never keyframes, easing, or a default duration. Scope entrance styling to `html[data-omlorix-mode="present"]` and initialize content in all runtime modes.

Buttons use `addEventListener`, not inline event attributes. A button with `data-slide-go="2"` opens the third slide. Arrow keys inside inputs, sliders, selects and other controls retain their normal behavior. Background arrows, Page Up/Down, Home/End and Space navigate; Escape closes the viewer and F toggles fullscreen. Embedded websites retain their own keyboard behavior; the outer navigation controls remain available.

## External content

Declare up to 16 exact public HTTPS origins per category:

```html
<meta name="omlorix-connect-src" content="https://api.example.org">
<meta name="omlorix-frame-src" content="https://embed.example.org">
<meta name="omlorix-img-src" content="https://images.example.org">
```

No wildcard hosts, userinfo, local/private IP literals, local hostnames or custom ports are accepted. The app hostname is excluded from the frame policy. This is browser access, not a backend API proxy: CORS, private-network protections and destination framing restrictions still apply. Do not include API keys, passwords or application credentials. Prefer `api.fetchJSON` over raw networking, handle errors in the deck’s language. URLs must come from supplied or verified sources.

Iframes require a title, explicit dimensions and a declared embed origin. They are sandboxed and loaded only while active. Some websites require capabilities this sandbox deliberately does not grant; use their documented embed URL; do not bypass framing or login restrictions.

## Live rendering and downloads

The sidebar and fullscreen editor offer **HTML source**, using the canonical presentation ID with the existing authenticated attachment-download endpoint. This preserves source scripts, metadata and assets without injecting runtime code. The editor saves pending changes before downloading HTML but does not wait for the renderer. HTML downloads retain file access enforcement, no-store headers, attachment disposition and the `FILE_DOWNLOADED` audit event.

`build_slide_render_document` sends the live source plus the shared runtime to the external browser renderer. JavaScript, declared public APIs, images and embeds execute there. All slides are visible in `render` mode, and each receives a slide-enter event. The resulting artifacts supply sidebar images, visual review, PDF, PPTX and image downloads. There is no authored snapshot projection or separate offline document. Exports start a fresh runtime session; they do not capture a viewer's current quiz answers or slider values.

Authors register asynchronous initialization through `api.waitUntil(promise)`, including from slide-enter handlers. Renderers can await `window.omlorixPresentationRenderReady` / `api.renderReady`, or wait for `html[data-omlorix-render-ready="true"]`, before capture. Registered work, embed load events, fonts and image decoding have a 30-second readiness ceiling. A rejected task or timeout rejects the promise and sets the readiness attribute to `error`. The renderer must execute JavaScript and support internet access; its artifact bundle API remains unchanged. Capturing after this readiness signal requires the external renderer's browser capture configuration to await it.

## Executable visual editor

The authenticated page hosts `/api/v1/presentations/editor/proxy`, a trusted static HTTP relay with an independent CSP. The relay mounts the complete editor in a **data URL**, which always has an opaque origin. That iframe uses `allow-scripts allow-same-origin` so its nested about:blank canvas can share the editor's opaque origin for DOM editing. The canvas is written in place, avoiding navigation to another opaque origin. The outer data editor must never be changed to srcdoc, an application HTTP URL or an application-origin blob URL. Authored content is never inserted into the trusted relay's DOM.

`POST /api/v1/presentations/{id}/editor/prepare` verifies ownership and validates unsaved source, returning its execution policy and runtime. Shared styles, icons, translations and theme values come from the authenticated host. The canvas runs JavaScript, loads declared content, and exposes generated DOM elements to the existing visual inspector. Code edits and undo/redo save and restart the isolated editor, preserving history and active slide while discarding old JavaScript handlers. Initialization should be idempotent because visual edits serialize the live DOM.

The message bridge checks the exact frame, origin and per-mount channel. It exposes only the current presentation's prepare/save/render, present, download and close operations; authenticated requests remain in the host. Pending renders survive script restarts. Theme, translations, keyboard focus and the existing accessible discard confirmation are relayed through the host. No new persistent user data is introduced.
