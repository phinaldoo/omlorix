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
    db.query.return_value.filter.return_value.all.return_value = [("file-1", "viewer")]
    payload = files_router._decorate_accessible_file_records(db, "subscriber-1", [_file_record()])

    assert len(payload) == 1
    shared_file = payload[0]
    assert shared_file.user_id is None
    assert shared_file.can_edit is False
    assert shared_file.can_delete is False
    assert "shared_owner_id" not in shared_file.meta
    assert "shared_contributor_id" not in shared_file.meta






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
