import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

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

from app.admin.groups import router as groups_router


def test_export_groups_route_writes_audit_log(monkeypatch):
    export_payload = {
        "export_type": "group",
        "export_version": 1.0,
        "data": {
            "groups": [
                {"id": "group-1", "name": "Alpha"},
                {"id": "group-2", "name": "Beta"},
            ],
            "group_managers": [
                {"group_id": "group-1", "user_id": "owner-1", "role": "owner"},
                {"group_id": "group-2", "user_id": "manager-1", "role": "manager"},
            ],
        },
    }
    audit_log_mock = MagicMock()

    monkeypatch.setattr(groups_router, "export_groups_util", lambda _db: export_payload)
    monkeypatch.setattr(groups_router, "create_audit_log", audit_log_mock)

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    db = object()
    db_log = object()
    admin_user = SimpleNamespace(id="admin-1")

    result = groups_router.export_groups_route(request, db, db_log, admin_user)

    assert result == export_payload
    audit_log_mock.assert_called_once_with(
        db_log=db_log,
        user_id="admin-1",
        action="EXPORT_GROUPS",
        details={
            "export_version": 1.0,
            "exported_count": 2,
            "exported_manager_assignment_count": 2,
            "sensitivity_category": "group_configuration",
        },
        ip_address="203.0.113.10",
        user_agent="pytest",
        category="group",
    )
