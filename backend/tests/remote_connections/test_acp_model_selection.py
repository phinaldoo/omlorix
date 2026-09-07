"""Persistence coverage for the per-chat ACP model selector."""

from types import SimpleNamespace

from app.remote_connections import service as service_module


class _Query:
    """Minimal SQLAlchemy query double for the owned chat lookup."""

    def __init__(self, result):
        self.result = result

    def filter(self, *_conditions):
        return self

    def first(self):
        return self.result


class _Db:
    """Capture the commit performed after updating mutable JSON metadata."""

    def __init__(self, chat):
        self.chat = chat
        self.committed = False

    def query(self, _model):
        return _Query(self.chat)

    def add(self, _row):
        return None

    def commit(self):
        self.committed = True


def test_save_acp_model_selection_preserves_other_session_metadata(monkeypatch):
    """Saving one ACP choice keeps unrelated chat metadata and sessions intact."""
    chat = SimpleNamespace(
        id="chat-one",
        user_id="user-one",
        meta={
            "project_marker": "keep",
            "acp_sessions": {"other-model": {"session_id": "other-session"}},
        },
    )
    db = _Db(chat)
    profile = SimpleNamespace(
        provider_id="provider-one",
        workspace_root="/srv/project",
        cwd=".",
    )
    monkeypatch.setattr(
        service_module,
        "get_terminal_target_for_model",
        lambda *_args, **_kwargs: (profile, SimpleNamespace(id="connection-one")),
    )
    monkeypatch.setattr(service_module, "flag_modified", lambda *_args: None)

    service_module.save_acp_model_selection(
        db,
        "user-one",
        chat_id="chat-one",
        model_id="model-one",
        session_id="session-one",
        acp_model_id="gpt-5.6-sol",
        acp_security_level="agent",
        acp_reasoning_effort="high",
    )

    assert db.committed
    assert chat.meta["project_marker"] == "keep"
    assert chat.meta["acp_sessions"]["other-model"]["session_id"] == "other-session"
    assert chat.meta["acp_sessions"]["model-one"] == {
        "session_id": "session-one",
        "provider_id": "provider-one",
        "cwd": "/srv/project",
        "selected_model_id": "gpt-5.6-sol",
        "selected_security_level": "agent",
        "selected_reasoning_effort": "high",
        "updated_at": chat.meta["acp_sessions"]["model-one"]["updated_at"],
    }


def test_model_change_drops_previous_reasoning_effort(monkeypatch):
    """Reasoning from one ACP model cannot leak into a different model."""
    chat = SimpleNamespace(
        id="chat-one",
        user_id="user-one",
        meta={
            "acp_sessions": {
                "model-one": {
                    "session_id": "session-one",
                    "provider_id": "provider-one",
                    "cwd": "/srv/project",
                    "selected_model_id": "old-model",
                    "selected_reasoning_effort": "high",
                }
            }
        },
    )
    db = _Db(chat)
    profile = SimpleNamespace(
        provider_id="provider-one",
        workspace_root="/srv/project",
        cwd=".",
    )
    monkeypatch.setattr(
        service_module,
        "get_terminal_target_for_model",
        lambda *_args, **_kwargs: (profile, SimpleNamespace(id="connection-one")),
    )
    monkeypatch.setattr(service_module, "flag_modified", lambda *_args: None)

    service_module.save_acp_model_selection(
        db,
        "user-one",
        chat_id="chat-one",
        model_id="model-one",
        session_id="session-one",
        acp_model_id="new-model",
    )

    saved = chat.meta["acp_sessions"]["model-one"]
    assert saved["selected_model_id"] == "new-model"
    assert "selected_reasoning_effort" not in saved


def test_disabling_ssh_connection_disables_dependent_models(monkeypatch):
    """An inactive SSH transport cannot leave dependent ACP models selectable."""
    connection = SimpleNamespace(id="connection-one", enabled=True)
    profile = SimpleNamespace(
        id="profile-one",
        user_id="user-one",
        ssh_connection_id=connection.id,
        enabled=True,
        updated_at=None,
    )
    synced = []

    class Query:
        def filter(self, *_conditions):
            return self

        def all(self):
            return [profile]

    class Db:
        committed = False

        def query(self, _model):
            return Query()

        def commit(self):
            self.committed = True

        def refresh(self, _row):
            return None

    db = Db()
    monkeypatch.setattr(
        service_module,
        "get_owned_ssh_connection",
        lambda *_args: connection,
    )
    monkeypatch.setattr(
        service_module,
        "_connection_values",
        lambda *_args: {"enabled": False},
    )
    monkeypatch.setattr(
        service_module,
        "_ssh_transport_changed",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        service_module,
        "_sync_acp_model",
        lambda _db, dependent: synced.append(dependent),
    )

    service_module.save_ssh_connection(
        db,
        "user-one",
        SimpleNamespace(),
        connection_id=connection.id,
    )

    assert db.committed
    assert profile.enabled is False
    assert synced == [profile]
