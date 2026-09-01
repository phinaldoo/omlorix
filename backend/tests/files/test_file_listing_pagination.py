import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

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


def _file_record(index: int, *, user_id: str = "user-1", folder_id: str | None = None) -> Files:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Files(
        id=f"file-{index}",
        user_id=user_id,
        file_name=f"stored-{index}.txt",
        storage_provider="local",
        storage_key=f"user-1/stored-{index}.txt",
        file_category="document",
        file_type="text/plain",
        file_size=index + 1,
        folder_id=folder_id,
        meta={"original_filename": f"Report {index}.txt", "origin": "user"},
        created_at=created_at,
        last_updated_at=created_at,
    )


def test_file_list_route_pages_accessible_files_without_legacy_full_list_helper():
    db = _db_session()
    db.add_all([_file_record(0), _file_record(1), _file_record(2)])
    db.commit()

    payload = files_router.get_files_route(
        limit=2,
        offset=1,
        user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert [item.file_id for item in payload] == ["file-1", "file-2"]
    assert all("origin" not in (item.meta or {}) for item in payload)


def test_file_list_route_without_limit_uses_default_page_size():
    db = _db_session()
    db.add_all([_file_record(index) for index in range(files_router.FILES_LIST_DEFAULT_LIMIT + 1)])
    db.commit()

    payload = files_router.get_files_route(
        limit=None,
        offset=0,
        user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert len(payload) == files_router.FILES_LIST_DEFAULT_LIMIT


def test_workspace_route_counts_and_pages_in_sql_without_full_accessible_list():
    db = _db_session()
    db.add_all([
        _file_record(0, folder_id=None),
        _file_record(1, folder_id="folder-1"),
        _file_record(2, folder_id="folder-1"),
    ])
    db.commit()

    payload = files_router.get_workspace_files_route(
        limit=2,
        offset=0,
        sort_field="name",
        sort_direction="asc",
        user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert payload.total == 3
    assert payload.counts.all == 3
    assert payload.counts.uncategorized == 1
    assert payload.counts.folders == {"folder-1": 2}
    assert len(payload.items) == 2
    assert payload.has_more is True


def test_workspace_route_sorts_by_created_at_for_file_pickers():
    db = _db_session()
    db.add_all([_file_record(0), _file_record(1), _file_record(2)])
    db.commit()

    payload = files_router.get_workspace_files_route(
        limit=2,
        offset=0,
        sort_field="created_at",
        sort_direction="desc",
        user=SimpleNamespace(id="user-1"),
        db=db,
    )

    assert [item.file_id for item in payload.items] == ["file-2", "file-1"]
