import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


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


from app.file_folders import router as file_folders_router
from app.files import router as files_router


def _file_record(**overrides):
    values = {
        "id": "file-1",
        "user_id": "owner-1",
        "file_name": "report.txt",
        "file_category": "document",
        "file_type": "text/plain",
        "file_size": 123,
        "project_id": None,
        "folder_id": "folder-1",
        "created_at": datetime.now(timezone.utc),
        "meta": {
            "shared_owner_id": "owner-1",
            "shared_contributor_id": "contributor-1",
            "original_filename": "report.txt",
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_accessible_shared_files_do_not_expose_stable_user_ids():
    db = MagicMock()
    folder = SimpleNamespace(id="folder-1", user_id="owner-1", name="Shared Docs")
    subscription = SimpleNamespace(share_type="live")

    with patch.object(
        files_router,
        "_fetch_file_folders_by_id",
        return_value={"folder-1": folder},
    ), patch.object(
        files_router,
        "_fetch_subscriptions_by_folder",
        return_value={"folder-1": (folder, subscription)},
    ), patch.object(
        files_router,
        "get_user",
        return_value=SimpleNamespace(first_name="Owner", last_name="User", email="owner@example.test"),
    ):
        payload = files_router._decorate_accessible_file_records(db, "subscriber-1", [_file_record()])

    assert len(payload) == 1
    shared_file = payload[0]
    assert shared_file.user_id is None
    assert shared_file.meta["shared_owner_name"] == "Owner User"
    assert "shared_owner_id" not in shared_file.meta
    assert "shared_contributor_id" not in shared_file.meta


def test_subscribed_folder_file_listing_minimizes_file_owner_id():
    shared_file = _file_record()
    query = MagicMock()
    query.all.return_value = [shared_file]

    with patch.object(file_folders_router, "get_file_folder", return_value=None), patch.object(
        file_folders_router,
        "can_user_access_folder",
        return_value=True,
    ), patch.object(
        file_folders_router,
        "accessible_folder_files_query",
        return_value=query,
    ):
        payload = file_folders_router.get_folder_files_route(
            folder_id="folder-1",
            db=MagicMock(),
            user=SimpleNamespace(id="subscriber-1"),
        )

    assert len(payload) == 1
    assert payload[0].user_id is None
    assert "shared_owner_id" not in payload[0].meta
    assert "shared_contributor_id" not in payload[0].meta


def test_subscribed_folder_metadata_does_not_expose_owner_user_id():
    folder = SimpleNamespace(
        id="folder-1",
        user_id="owner-1",
        name="Shared Docs",
        icon="folder",
        icon_color="#6366f1",
        order=0,
        clone_share_id="clone-share-1",
        live_share_id="live-share-1",
        collaborate_share_id="collaborate-share-1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 3

    with patch.object(file_folders_router, "get_folder_subscriber_count", return_value=1), patch.object(
        file_folders_router,
        "count_accessible_folder_files",
        return_value=3,
    ):
        subscribed = file_folders_router._folder_to_response(
            folder,
            db,
            is_subscribed=True,
            share_type="live",
            owner_name="Owner User",
            viewer_user_id="subscriber-1",
        )
        owned = file_folders_router._folder_to_response(folder, db)

    assert subscribed.user_id is None
    assert subscribed.owner_name == "Owner User"
    assert subscribed.clone_share_id is None
    assert subscribed.live_share_id is None
    assert subscribed.collaborate_share_id is None
    assert subscribed.subscriber_count is None
    assert owned.user_id == "owner-1"
    assert owned.clone_share_id == "clone-share-1"
    assert owned.live_share_id == "live-share-1"
    assert owned.collaborate_share_id == "collaborate-share-1"
    assert owned.subscriber_count == 1


def test_project_file_listing_minimizes_other_user_file_owner_id():
    own_file = _file_record(id="own-file", user_id="user-1")
    other_file = _file_record(id="other-file", user_id="other-user")

    with patch.object(files_router, "list_project_files", return_value=[own_file, other_file]):
        payload = files_router.get_project_files_route(
            project_id="project-1",
            db=MagicMock(),
            user=SimpleNamespace(id="user-1"),
        )

    assert payload[0].user_id == "user-1"
    assert payload[1].user_id is None
    assert "shared_owner_id" not in payload[1].meta
    assert "shared_contributor_id" not in payload[1].meta
