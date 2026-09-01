import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import models as auth_models
from app.logging import worker as retention_worker


def test_process_password_reset_token_retention_runs_cleanup(monkeypatch):
    calls = []

    def fake_cleanup(db):
        calls.append(db)
        return 2

    monkeypatch.setattr(auth_models, "delete_expired_password_reset_tokens", fake_cleanup)

    assert retention_worker._process_password_reset_token_retention("main-db-session") is True
    assert calls == ["main-db-session"]
