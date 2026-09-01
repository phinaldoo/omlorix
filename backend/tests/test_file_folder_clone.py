from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base  # noqa: E402
from app.file_folders import models as folder_models  # noqa: E402
from app.files import utils as file_utils  # noqa: E402
from app.file_folders.models import FileFolders  # noqa: E402
from app.files.models import Files  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[FileFolders.__table__, Files.__table__])
    return sessionmaker(bind=engine)()


def test_clone_shared_folder_copies_files_as_independent_records(monkeypatch):
    db = _session()
    now = datetime.now(timezone.utc)
    source_folder = FileFolders(
        id="folder-1",
        user_id="owner-1",
        name="Research",
        icon="folder",
        icon_color="#111111",
        order=0,
        clone_share_id="clone-share-1",
        created_at=now,
        updated_at=now,
    )
    source_file = Files(
        id="file-1",
        user_id="owner-1",
        file_name="source.txt",
        storage_provider="local",
        storage_key="owner-1/source.txt",
        storage_meta={"etag": "source"},
        file_category="document",
        file_type="text/plain",
        file_size=12,
        project_id="project-1",
        folder_id="folder-1",
        share={"enabled": True},
        share_id="file-share-1",
        meta={
            "original_filename": "source.txt",
            "sha256": "abc123",
            "share_id": "meta-share-1",
            "shared_owner_id": "owner-1",
            "nested": {
                "label": "safe",
                "access_token": "secret-token",
            },
        },
        created_at=now,
        last_updated_at=now,
    )
    db.add(source_folder)
    db.add(source_file)
    db.commit()

    copied = []

    def fake_copy(source, source_user_id, target_user_id, target_file_name):
        copied.append((source.id, source_user_id, target_user_id, target_file_name))
        return "local", f"{target_user_id}/{target_file_name}", {"copied": True}

    monkeypatch.setattr(folder_models, "_copy_file_storage_for_clone", fake_copy)
    monkeypatch.setattr(file_utils, "resolve_user_file_upload_limits", lambda _db, _user_id: (-1, None))
    monkeypatch.setattr(file_utils, "ensure_user_file_upload_size_limit", lambda _db, _user_id, _size: None)

    cloned_folder = folder_models.clone_shared_folder(db, "viewer-1", "clone-share-1")

    cloned_files = db.query(Files).filter(Files.user_id == "viewer-1").all()
    assert len(cloned_files) == 1
    cloned_file = cloned_files[0]
    assert copied == [("file-1", "owner-1", "viewer-1", cloned_file.file_name)]
    assert cloned_folder.id != source_folder.id
    assert cloned_file.id != source_file.id
    assert cloned_file.folder_id == cloned_folder.id
    assert cloned_file.project_id is None
    assert cloned_file.share is None
    assert cloned_file.share_id is None
    assert cloned_file.storage_key == f"viewer-1/{cloned_file.file_name}"
    assert cloned_file.storage_key != source_file.storage_key
    assert cloned_file.meta == {
        "original_filename": "source.txt",
        "sha256": "abc123",
        "nested": {"label": "safe"},
    }
