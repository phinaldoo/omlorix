"""Atomic import coverage for user-owned SSH and ACP definitions."""

from app.remote_connections import service as service_module
from app.users import utils as user_utils


class _ImportDb:
    """Minimal unit-of-work double that assigns IDs during flush."""

    def __init__(self):
        self.added = []
        self.commit_count = 0

    def add(self, row):
        self.added.append(row)

    def flush(self):
        row = self.added[-1]
        if not row.id:
            row.id = f"restored-{len(self.added)}"

    def commit(self):
        self.commit_count += 1


def test_remote_connection_import_maps_only_unique_ids_and_commits_once(
    monkeypatch,
):
    """SSH rows and every valid dependent profile share one final commit."""
    db = _ImportDb()
    saved_profiles = []

    def fake_save_acp_profile(_db, user_id, payload, *, commit=True):
        saved_profiles.append((user_id, payload, commit, db.commit_count))

    monkeypatch.setattr(service_module, "save_acp_profile", fake_save_acp_profile)
    connections = [
        {"id": "unique", "name": "Unique", "host": "one.test"},
        {"id": "duplicate", "name": "Duplicate one", "host": "two.test"},
        {"id": "duplicate", "name": "Duplicate two", "host": "three.test"},
        {"id": "", "name": "No source ID", "host": "four.test"},
    ]
    profiles = [
        {
            "name": "Mapped",
            "ssh_connection_id": "unique",
            "workspace_root": "/srv/project",
        },
        {
            "name": "Ambiguous",
            "ssh_connection_id": "duplicate",
            "workspace_root": "/srv/project",
        },
        {
            "name": "Missing",
            "ssh_connection_id": "",
            "workspace_root": "/srv/project",
        },
    ]

    user_utils._bulk_insert_remote_connections(
        db,
        "user-one",
        connections,
        profiles,
    )

    assert [row.name for row in db.added] == ["Unique", "No source ID"]
    assert len(saved_profiles) == 1
    user_id, payload, commit, commit_count_during_save = saved_profiles[0]
    assert user_id == "user-one"
    assert payload.ssh_connection_id == "restored-1"
    assert commit is False
    assert commit_count_during_save == 0
    assert db.commit_count == 1
