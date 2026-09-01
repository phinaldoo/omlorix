import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard


from app.files import sharing
from app.users import router as users_router


class _FakeQuery:
    def __init__(self, db, model_names):
        self.db = db
        self.model_names = model_names

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def all(self):
        if self.model_names == ("FileArtifactShare",):
            return list(self.db.artifact_shares)
        if self.model_names == ("FileArtifactShare", "Files"):
            return list(self.db.artifact_rows)
        return []

    def first(self):
        if self.model_names == ("Files",):
            return self.db.file_record
        if self.model_names == ("FileArtifactShare",):
            return self.db.artifact_shares[0] if self.db.artifact_shares else None
        return None


class _FakeDb:
    def __init__(self, *, file_record=None, artifact_shares=None, artifact_rows=None, query_failures=None):
        self.file_record = file_record
        self.artifact_shares = list(artifact_shares or [])
        self.artifact_rows = list(artifact_rows or [])
        self.query_failures = dict(query_failures or {})
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, *models):
        model_names = tuple(getattr(model, "__name__", str(model)) for model in models)
        failure = self.query_failures.get(model_names)
        if failure is not None:
            raise failure
        return _FakeQuery(self, model_names)

    def delete(self, share):
        self.deleted.append(share)
        self.artifact_shares = [row for row in self.artifact_shares if row is not share]
        self.artifact_rows = [row for row in self.artifact_rows if row[0] is not share]

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _artifact_share(*, share_id: str, expires_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=share_id,
        file_id="file-1",
        user_id="user-1",
        password_hash=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=expires_at,
        last_accessed_at=None,
        access_count=0,
    )


def test_get_artifact_share_status_remains_available_when_new_shares_are_disabled(monkeypatch):
    file_record = SimpleNamespace(
        id="file-1",
        user_id="user-1",
        file_name="artifact.md",
        file_type="text/markdown",
        meta={"canvas": True},
    )
    expired_share = _artifact_share(
        share_id="share-expired",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    active_share = _artifact_share(
        share_id="share-active",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db = _FakeDb(file_record=file_record, artifact_shares=[expired_share, active_share])

    def fail_if_creation_policy_is_checked(*_args, **_kwargs):
        raise AssertionError("share-status reads must not enforce the share-creation policy")

    monkeypatch.setattr(
        sharing,
        "ensure_artifact_file_sharing_allowed_for_user",
        fail_if_creation_policy_is_checked,
    )
    monkeypatch.setattr(sharing, "get_public_url", lambda _db: "https://chat.example")

    result = sharing.get_artifact_share_status(db=db, user_id="user-1", file_id="file-1")

    assert [link["share_id"] for link in result["links"]] == ["share-active"]
    assert db.deleted == [expired_share]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_expired_artifact_share_does_not_count_as_existing_share_state():
    expired_share = _artifact_share(
        share_id="share-expired",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db = _FakeDb(artifact_shares=[expired_share])

    assert sharing.artifact_file_has_existing_share_state(
        db=db,
        user_id="user-1",
        file_id="file-1",
    ) is False


def test_shared_items_route_excludes_expired_artifact_shares(monkeypatch):
    active_share = _artifact_share(
        share_id="share-active",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    expired_share = _artifact_share(
        share_id="share-expired",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    file_record = SimpleNamespace(id="file-1", file_name="artifact.md")
    db = _FakeDb(
        artifact_shares=[expired_share, active_share],
        artifact_rows=[(expired_share, file_record), (active_share, file_record)],
    )

    monkeypatch.setattr(users_router, "build_shared_item_url", lambda base_url, item_type, share_id, share_type=None: f"{base_url}/{item_type}/{share_id}")
    monkeypatch.setattr(users_router, "get_shared_item_capabilities", lambda item_type: {"type": item_type})

    import app.settings.utils as settings_utils

    monkeypatch.setattr(settings_utils, "get_public_url", lambda _db: "https://chat.example")

    result = users_router.get_shared_items_route(db=db, user=SimpleNamespace(id="user-1"))

    artifact_items = [item for item in result["items"] if item["type"] == "artifact"]

    assert result["status"] == "ok"
    assert result["section_errors"] == []
    assert [item["share_id"] for item in artifact_items] == ["share-active"]
    assert db.deleted == [expired_share]
    assert db.commits == 1


def test_shared_items_route_reports_degraded_status_when_inventory_is_partial(monkeypatch):
    active_share = _artifact_share(
        share_id="share-active",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    file_record = SimpleNamespace(id="file-1", file_name="artifact.md")
    db = _FakeDb(
        artifact_shares=[active_share],
        artifact_rows=[(active_share, file_record)],
        query_failures={("Chats",): RuntimeError("database unavailable")},
    )

    monkeypatch.setattr(users_router, "build_shared_item_url", lambda base_url, item_type, share_id, share_type=None: f"{base_url}/{item_type}/{share_id}")
    monkeypatch.setattr(users_router, "get_shared_item_capabilities", lambda item_type: {"type": item_type})

    import app.settings.utils as settings_utils

    monkeypatch.setattr(settings_utils, "get_public_url", lambda _db: "https://chat.example")

    result = users_router.get_shared_items_route(db=db, user=SimpleNamespace(id="user-1"))

    artifact_items = [item for item in result["items"] if item["type"] == "artifact"]

    assert result["status"] == "degraded"
    assert result["section_errors"] == [
        {"section": "chat", "code": "inventory_unavailable"},
    ]
    assert [item["share_id"] for item in artifact_items] == ["share-active"]
