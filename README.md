# Study visualization browser evidence

These are unedited T3 collaborative browser screenshots of authored quiz/flashcard examples passed through Omlorix’s production visualization validator, sandbox, host toolbar, renderer, and saved-state service. This is a fixture page using the application assets, not a live-model generation or an authenticated full-app session. A disposable SQLite database and fixed fixture identity stand in for production authentication/PostgreSQL. No provider was called.

The screenshots show quiz feedback, a flashcard restored after a page reload (including its revealed answer and mastery progress), and a 320 CSS-pixel dark-mode flashcard. The desktop viewport was 1180 × 1000 CSS pixels; screenshot dimensions depend on the browser’s capture scale.

## Reproduce

With backend dependencies installed, run `python3 /path/to/evidence/serve.py` from the Omlorix implementation checkout, or set `OMLORIX_REPO_ROOT`. Open `http://127.0.0.1:8975/?example=quiz&verify` or `?example=flashcards&verify`. The optional `verify` query adds a test-only DOM probe inside the production opaque sandbox. `check-dispatch.py`, run from the repository root, verifies both fragments through the actual `create_visualization` dispatcher with validate/render actions, bypassing rate admission only for the fixture.

Verified quiz paths: correct/incorrect answers, duplicate submission, score, retry missed, completion, reset, reload, and no horizontal overflow at 320px. Verified flashcard paths: reveal, Again/Hard bounded queues, Got it mastery, shuffle, reverse/reset, completion, reload, and dark/mobile layout. Source examples are supplied for reproducibility; generated interfaces depend on the chosen model and user request.

All files on this evidence branch are separate from the implementation PR’s tree so screenshots and demo fixtures do not ship in application documentation.
