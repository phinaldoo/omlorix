# Shared frontend helpers

The frontend uses classic deferred scripts. Load shared helpers before their consumers in each HTML entry page; a new entry page must declare the same dependencies explicitly.

- `admin/helper/fieldLayout.js` owns schema section shells and model input constraints. Create/edit pages retain value initialization, bulk-edit state, and field rendering. `common/dependencyUtils.js` compares dependency values; callers retain their visibility and field-lookup rules.
- `admin/helper/selectControls.js` owns provider URL suggestions, including URL normalization, manual input synchronization, translated labels, and custom-select upgrades.
- `admin/mediaGenerationCommon.js` owns media API clients, native field creation, field value conversion/binding, status notifications, and select placeholders. Each page supplies its current abort signal and retains provider-specific settings, voice discovery, dependencies, and autosave lifecycle.
- `common/textPreview.js` reads size-limited text from an existing response. File previews and shared-chat previews supply their own byte limits and retain their own authentication and download routing. The helper handles range metadata, streamed UTF-8 decoding, cancellation, and blob fallback.
- `common/publicUsers.js` supplies a bounded directory request and invitation-picker lifecycle for Todos, Skills, Notes, Folders, and Prompts. Opening a picker requests at most 100 users. Search is debounced and sent through the existing `q` parameter; **Load more** requests the next page. Selected users survive searches, duplicate IDs are removed, and superseded or closed pickers abort requests and ignore late responses. Feature modules retain invitation permissions, translated errors, rendering, and selected-user state.

Directory paging bounds frontend requests and retained search results. The current backend sharing-discovery implementation still filters candidate users in memory before slicing a page; this frontend refactor does not make that backend query suitable for million-user directories.

`frontend/js/common/sharedHelpers.test.js` checks script ordering and runs the shared form, picker, and preview contracts in Chromium through the repository's Electron test runtime.
