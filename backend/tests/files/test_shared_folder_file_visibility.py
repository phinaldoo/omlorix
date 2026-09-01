import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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


from app.database import Base
from app.file_folders import models as folder_models
from app.file_folders import router as file_folders_router
from app.file_folders.models import FileFolders, SharedFileFolderSubscription
from app.files import router as files_router
from app.files.models import Files


def _db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Files.__table__,
            FileFolders.__table__,
            SharedFileFolderSubscription.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)
    return Session()


def _folder(**overrides) -> FileFolders:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {
        "id": "folder-1",
        "user_id": "owner-1",
        "name": "Shared Docs",
        "icon": "folder",
        "icon_color": "#6366f1",
        "order": 0,
        "live_share_id": "live-share-1",
        "collaborate_share_id": "collab-share-1",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return FileFolders(**values)


def _subscription(subscriber_id: str, share_type: str) -> SharedFileFolderSubscription:
    return SharedFileFolderSubscription(
        id=f"{subscriber_id}-{share_type}",
        folder_id="folder-1",
        subscriber_id=subscriber_id,
        share_type=share_type,
        subscribed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _file(index: int, user_id: str, folder_id: str = "folder-1") -> Files:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Files(
        id=f"file-{index}",
        user_id=user_id,
        file_name=f"stored-{index}.txt",
        storage_provider="local",
        storage_key=f"{user_id}/stored-{index}.txt",
        file_category="document",
        file_type="text/plain",
        file_size=index + 1,
        folder_id=folder_id,
        meta={"original_filename": f"Report {index}.txt", "origin": "user"},
        created_at=now,
        last_updated_at=now,
    )


def _seed_shared_folder(db):
    db.add(_folder())
    db.add_all([
        _subscription("live-user", "live"),
        _subscription("collab-user", "collaborate"),
    ])
    db.add_all([
        _file(1, "owner-1"),
        _file(2, "peer-user"),
        _file(3, "collab-user"),
    ])
    db.commit()


def test_owner_folder_listing_and_count_include_collaborator_files():
    db = _db_session()
    _seed_shared_folder(db)

    payload = file_folders_router.get_folder_files_route(
        folder_id="folder-1",
        db=db,
        user=SimpleNamespace(id="owner-1"),
    )
    response = file_folders_router._folder_to_response(
        db.query(FileFolders).filter(FileFolders.id == "folder-1").first(),
        db,
        viewer_user_id="owner-1",
    )

    assert [item.file_id for item in payload] == ["file-1", "file-2", "file-3"]
    assert response.file_count == 3


def test_collaborative_subscriber_folder_listing_includes_all_folder_files():
    db = _db_session()
    _seed_shared_folder(db)

    payload = file_folders_router.get_folder_files_route(
        folder_id="folder-1",
        db=db,
        user=SimpleNamespace(id="collab-user"),
    )
    response = file_folders_router._folder_to_response(
        db.query(FileFolders).filter(FileFolders.id == "folder-1").first(),
        db,
        is_subscribed=True,
        share_type="collaborate",
        viewer_user_id="collab-user",
    )

    assert [item.file_id for item in payload] == ["file-1", "file-2", "file-3"]
    assert response.file_count == 3
    assert payload[0].user_id is None
    assert payload[1].user_id is None
    assert payload[2].user_id == "collab-user"


def test_live_subscriber_folder_listing_stays_owner_file_only():
    db = _db_session()
    _seed_shared_folder(db)

    payload = file_folders_router.get_folder_files_route(
        folder_id="folder-1",
        db=db,
        user=SimpleNamespace(id="live-user"),
    )
    response = file_folders_router._folder_to_response(
        db.query(FileFolders).filter(FileFolders.id == "folder-1").first(),
        db,
        is_subscribed=True,
        share_type="live",
        viewer_user_id="live-user",
    )

    assert [item.file_id for item in payload] == ["file-1"]
    assert response.file_count == 1
    assert payload[0].user_id is None


def test_single_file_metadata_uses_same_shared_folder_visibility_rules():
    db = _db_session()
    _seed_shared_folder(db)

    with patch.object(
        files_router,
        "get_user",
        return_value=SimpleNamespace(first_name="Shared", last_name="User", email="shared@example.test"),
    ):
        collaborative_payload = files_router.get_file_route(
            file_id="file-2",
            user=SimpleNamespace(id="collab-user"),
            db=db,
        )

    assert collaborative_payload.file_id == "file-2"
    assert collaborative_payload.user_id is None

    with pytest.raises(HTTPException) as exc:
        files_router.get_file_route(
            file_id="file-2",
            user=SimpleNamespace(id="live-user"),
            db=db,
        )

    assert exc.value.status_code == 404


def test_collaborative_share_preview_count_matches_folder_visibility():
    db = _db_session()
    _seed_shared_folder(db)

    with patch.object(folder_models, "_get_owner_display_name", return_value="Owner User"):
        preview = folder_models.get_shared_folder_preview(db, "collab-share-1", requesting_user_id="viewer-1")

    assert preview["share_type"] == "collaborate"
    assert preview["file_count"] == 3


def test_deleting_owned_folder_clears_collaborator_file_folder_ids():
    db = _db_session()
    _seed_shared_folder(db)

    result = folder_models.delete_file_folder(db, "owner-1", "folder-1")

    assert result == {"ok": True}
    assert db.query(FileFolders).filter(FileFolders.id == "folder-1").first() is None
    assert db.query(SharedFileFolderSubscription).filter(
        SharedFileFolderSubscription.folder_id == "folder-1"
    ).count() == 0
    assert db.query(Files).filter(Files.id == "file-1").first().folder_id is None
    assert db.query(Files).filter(Files.id == "file-2").first().folder_id is None
    assert db.query(Files).filter(Files.id == "file-3").first().folder_id is None
