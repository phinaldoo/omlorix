import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

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

from app.agents.models import SharedUserAgentSubscription, UserAgent, UserAgentAsset
from app.chats.models import Chats
from app.file_folders.models import FileFolders
from app.files.models import Files
from app.groups.models import GroupManager
from app.notes.models import Notes
from app.projects.models import Project
from app.prompts.models import Prompts
from app.scim.models import ScimGroupMembership, ScimUserLink
from app.skills.models import Skills
from app.todos.models import TodoLists
from app.tools.slide_presentation.models import SlidePresentations
from app.userNotifications.models import UserNotifications
from app.users.models import User, hard_delete_user


class _FakeQuery:
    def __init__(self, db, key):
        self._db = db
        self._key = key

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def delete(self, synchronize_session=False):
        self._db.bulk_deletes.append(self._key)
        return 0

    def update(self, _values, synchronize_session=False):
        return len(self._db.query_results.get(self._key, []))

    def all(self):
        return list(self._db.query_results.get(self._key, []))

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class _FakeDb:
    def __init__(self, query_results):
        self.query_results = query_results
        self.bulk_deletes = []
        self.deleted_objects = []
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        return _FakeQuery(self, self._normalize_query_key(model))

    def execute(self, _statement):
        return SimpleNamespace(rowcount=0)

    def delete(self, obj):
        self.deleted_objects.append(obj)

    def flush(self):
        """Mirror the SQLAlchemy Session method used to enforce delete ordering."""

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    @staticmethod
    def _normalize_query_key(model):
        class_ = getattr(model, "class_", None)
        key = getattr(model, "key", None)
        if class_ is not None and key is not None:
            return (class_, key)
        return model


def test_hard_delete_admin_requires_explicit_owner_authorization(monkeypatch):
    """The model guard permits an admin only after the owner-authorized route."""

    user = SimpleNamespace(id="admin-1", role="admin")
    db = _FakeDb({User: [user]})

    with pytest.raises(HTTPException) as exc_info:
        hard_delete_user(db, user.id)

    assert exc_info.value.status_code == 409
    assert db.deleted_objects == []

    import app.auth.session_store as session_store

    monkeypatch.setattr(session_store, "revoke_user_sessions", lambda _user_id: None)

    assert hard_delete_user(
        db,
        user.id,
        allow_administrative_target=True,
        record_erasure=False,
        notify_user=False,
    ) is True
    assert user in db.deleted_objects
    assert db.committed is True


def test_hard_delete_user_cleans_newer_user_linked_records_and_storage(monkeypatch, tmp_path):
    user_id = "user-1"
    user = SimpleNamespace(id=user_id, role="user")

    agent = UserAgent(
        id="agent-1",
        user_id=user_id,
        name="Agent",
        icon="",
        base_model_id="model-1",
        instruction="",
    )
    owned_asset = UserAgentAsset(
        id="asset-1",
        agent_id=agent.id,
        owner_user_id=user_id,
        file_name="owned-asset.txt",
        storage_provider="local",
        storage_key="",
        file_category="document",
        file_type="text/plain",
        file_size=4,
    )
    shared_agent_asset = UserAgentAsset(
        id="asset-2",
        agent_id="agent-2",
        owner_user_id=user_id,
        file_name="shared-asset.txt",
        storage_provider="local",
        storage_key="",
        file_category="document",
        file_type="text/plain",
        file_size=4,
    )
    presentation = SlidePresentations(
        id="presentation-1",
        user_id=user_id,
        title="Deck",
        slide_count=1,
        storage_provider="local",
        storage_prefix=f"{user_id}/presentations/presentation-1",
        file_id=None,
    )
    delete_only_notification = UserNotifications(
        id="notif-delete",
        everyone=False,
        user_ids=f"|{user_id}|",
        group_ids=None,
        category="general",
        type="info",
        message="only deleted user",
    )
    kept_notification = UserNotifications(
        id="notif-keep",
        everyone=False,
        user_ids=f"|{user_id}|user-2|",
        group_ids=None,
        category="general",
        type="info",
        message="multiple users",
    )

    presentation_dir = tmp_path / user_id / "presentations" / presentation.id
    (presentation_dir / "images").mkdir(parents=True)
    (presentation_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (presentation_dir / "title.txt").write_text("Deck", encoding="utf-8")
    (presentation_dir / "presentation.html").write_text("<html></html>", encoding="utf-8")
    (presentation_dir / "images" / "slide_1.png").write_bytes(b"png")

    user_files_dir = tmp_path / user_id
    user_files_dir.mkdir(parents=True, exist_ok=True)
    (user_files_dir / owned_asset.file_name).write_text("asset", encoding="utf-8")
    (user_files_dir / shared_agent_asset.file_name).write_text("asset", encoding="utf-8")

    query_results = {
        User: [user],
        (Skills, "id"): [],
        (TodoLists, "id"): [],
        (Notes, "id"): [],
        (Chats, "id"): [],
        Files: [],
        (FileFolders, "id"): [],
        (Prompts, "id"): [],
        (Project, "id"): [],
        (UserAgent, "id"): [(agent.id,)],
        UserAgent: [agent],
        UserAgentAsset: [owned_asset, shared_agent_asset],
        SlidePresentations: [presentation],
        UserNotifications: [delete_only_notification, kept_notification],
    }
    db = _FakeDb(query_results)

    import app.auth.session_store as session_store
    import app.files.storage as file_storage
    import app.files.utils as file_utils
    import app.tools.slide_presentation.storage as presentation_storage

    monkeypatch.setattr(session_store, "revoke_user_sessions", lambda target_user_id: None)
    monkeypatch.setattr(file_storage, "get_local_user_files_base_dir", lambda: tmp_path)
    monkeypatch.setattr(file_utils, "BASE_STORAGE_DIR", tmp_path)
    materialized_dir = tmp_path / "materialized"
    materialized_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(presentation_storage, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(presentation_storage, "MATERIALIZED_TEMP_DIR", materialized_dir)

    result = hard_delete_user(
        db,
        user_id,
        record_erasure=False,
        notify_user=False,
    )

    assert result is True
    assert db.committed is True
    assert db.rolled_back is False
    assert user in db.deleted_objects
    assert agent in db.deleted_objects
    assert owned_asset in db.deleted_objects
    assert shared_agent_asset in db.deleted_objects
    assert presentation in db.deleted_objects
    assert delete_only_notification in db.deleted_objects
    assert kept_notification.user_ids == "|user-2|"
    assert not (user_files_dir / owned_asset.file_name).exists()
    assert not (user_files_dir / shared_agent_asset.file_name).exists()
    assert not presentation_dir.exists()
    assert SharedUserAgentSubscription in db.bulk_deletes
    assert ScimUserLink in db.bulk_deletes
    assert ScimGroupMembership in db.bulk_deletes
    assert GroupManager in db.bulk_deletes
