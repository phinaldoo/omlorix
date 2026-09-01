# AGENTS.md

## Project: Omlorix

Omlorix is a self-hosted, multi-user LLM chat web application. It has a FastAPI backend, a static JavaScript frontend, PostgreSQL storage, LLM provider integrations, user management, data import/export, audit logging, translations, and related chat features.

Omlorix should support high traffic, multi million user instances seemlessly and architecture and code should support this perfectly!

This file defines the expectations for AI coding agents working in this repository.

---

## 1. Core Principles

When making changes:

- Prefer clear, maintainable, boring code over clever code.
- Keep changes focused on the requested task.
- Reuse existing project patterns before introducing new ones.
- Do not silently skip related work such as translations, accessibility, migrations, tests, or import/export support.
- When code changes affect documented behavior, configuration, setup, APIs, architecture, or workflows, update the relevant documentation in the same change when applicable.
- Do not add excessive tests. Add targeted tests that cover the changed behavior.
- Try to reuse as much code as possible, so there is as little duplicate code as possible. Try to do things with as little LOC added as possible.

### Server Launcher App and CLI Feature Parity

The Electron Server Launcher App and the standalone `omlorix-server` CLI must always remain on feature parity.
When adding, changing, or removing a server-management feature in either the Launcher App or CLI, update the other surface in the same change.Reuse shared formats, environment keys, Compose files, defaults, validation rules, security boundaries, and backend commands so the App and CLI cannot drift semantically.
- Provide a terminal-appropriate CLI workflow for every App feature; do not omit a feature merely because the App presents it graphically.
- Provide an accessible App workflow for every CLI feature intended for ordinary server operators; low-level diagnostics or automation-only output may remain CLI-specific when appropriate.

---

## 2. Translations and Internationalization

All user-facing text must be translated.

This includes:

- Frontend UI labels, buttons, placeholders, headings, messages, tooltips, errors, warnings, empty states, and dialogs.
- The complete Electron Server Launcher, including onboarding, dashboard pages, settings, status and error messages, operation logs, dialogs, placeholders, tooltips, and accessibility labels.
- Backend-rendered schema labels, descriptions, enum labels, validation messages, and API-facing user-visible text.
- Settings pages, admin pages, chat UI elements, provider configuration UI, tool/skill UI, and import/export UI.

Rules:

- Translation keys must be hardcoded and stable.
- Do not dynamically generate translation keys using hashes, IDs, scripts, or runtime-generated names.
- Do not use scripts to copy English strings into other languages.
- Translate values properly into each supported language.
- Keep terminology consistent with existing translations.
- When editing existing UI text, update all language files.
- When adding new UI text, add the key to all language files.
- Launcher text must always be translated into every language supported by the main application; do not leave launcher-only English fallbacks for supported locales.
- Do not remove translation keys unless you also remove every usage.

---

## 3. Tests

Do not write too many tests.

---

## 4. Data Controls, Import, and Export

When changing backend code that saves or modifies persistent data, check whether that data must be importable and exportable.

This applies to:

* New database tables.
* New database columns.
* New persisted settings.
* New user preferences.
* New chat metadata.
* New provider/tool/skill configuration.
* New admin-managed data.
* New audit-relevant state.
* Any data that users or admins reasonably expect to back up, migrate, or restore.

---

## 5. Frontend Guidelines

The frontend is static JavaScript. Follow existing frontend patterns.

### Dialogs and Confirmations

* Do not use native browser dialogs such as `alert()`, `confirm()`, or `prompt()` in frontend code.
* Use accessible modal dialogs, inline validation, toasts, or existing confirmation components instead.
* Reuse existing modal and confirmation code before adding new dialog implementations.
* Ensure dialog text is translated and that focus management, keyboard navigation, and screen reader semantics are handled correctly.

### Accessibility

All frontend changes must include proper accessibility support.

### Styling

* Reuse existing CSS variables from `init.css`.
* Do not use hardcoded colors when a suitable CSS variable exists.
* Keep styling consistent with the existing design system.
* Prefer existing utility classes and component styles.
* Avoid unnecessary new global styles.

### Animations

* Put animation keyframes and reusable animation definitions in `animations.css`.
* Fully support reduced motion through `prefers-reduced-motion`.
* Do not add required functionality that depends on animation.
* Ensure UI remains usable when animations are disabled.

### Hover Behavior

Use `:hover` only on devices that support hover.

Good:

```css
@media (hover: hover) and (pointer: fine) {
  .action-button:hover {
    background: var(--surface-hover);
  }
}
```

Avoid unconditional hover rules that affect touch devices.

### SVG Icons

Please put all svg icon code into icons.js. 

---

## 6. Backend Guidelines

The backend uses FastAPI. Keep backend code organized by feature.

### Folder Structure

Each feature folder should generally follow this structure:

```text
/feature
  models.py     # Database models and persistence logic
  schemas.py    # Pydantic schemas and API response/request schemas
  router.py     # FastAPI endpoints with minimal business logic
  utils.py      # Feature-specific helper functions
  ...
```

Examples of feature folders:

```text
/users
/skills
/tools
/chats
/llm
```

Use additional files only when they make the feature easier to maintain.

### Router Guidelines

Routers should contain only necessary endpoint wiring.

Prefer:

* Request parsing.
* Dependency injection.
* Permission checks.
* Calling service/model/helper functions.
* Returning response models.

Avoid putting complex business logic directly in `router.py`.

### Schemas

* Use FastAPI response models for endpoints.
* Put request and response schemas in `schemas.py`.
* Keep schemas explicit.
* Validate inputs with Pydantic.
* Avoid returning raw database models unless the project already has a safe pattern for it.
* Ensure schemas do not expose secrets, internal IDs, or private fields unintentionally.
* Add translated schema labels/descriptions when schemas are rendered in the UI (e.g. Sections and Fields).

### Endpoints

For FastAPI endpoints:

* Use response models.
* Validate request bodies.
* Enforce authentication and authorization.
* Check ownership before accessing user-owned data.
* Avoid side effects in `GET` endpoints.
* Use appropriate HTTP methods.
* Return appropriate status codes.
* Keep error responses consistent with existing project conventions.
* Do not expose stack traces or internal exception details.
* Use pagination for potentially large lists.
* Avoid loading unbounded data.
* Keep endpoint behavior documented through schemas and clear naming.

### Audit Logging

Use proper audit logging for endpoints where it makes sense.

Audit logging is especially important for:

* Login, logout, and authentication-related events.
* User creation, deletion, role changes, and permission changes.
* Admin actions.
* Provider configuration changes.
* Tool or skill changes.
* Data import/export.
* Deleting chats or user data.
* Changing security-sensitive settings.
* Failed authorization attempts when relevant.

Audit logs should include enough context to be useful, without leaking secrets.

Do not log:

* Passwords.
* API keys.
* Access tokens.
* Refresh tokens.
* Full prompt contents unless the existing audit policy explicitly allows it.
* Sensitive user data unless required and already handled by existing conventions.

### Database and Models

When changing persistent data:

* Add migrations when required.
* Keep migrations safe and reversible where practical.
* Consider default values for existing rows.
* Avoid destructive migrations unless explicitly requested.
* Update import/export support where applicable.
* Update related schemas.
* Update API responses if the new data should be exposed.
* Update tests for persistence and migration-sensitive behavior where useful.

### Secrets and Sensitive Data

* Never expose secrets in API responses.
* Never include secrets in frontend state unless absolutely necessary.
* Mask secrets in UI.
* Avoid logging secrets.
* Store credentials according to existing project conventions.
* Preserve existing encryption, hashing, or masking behavior.
