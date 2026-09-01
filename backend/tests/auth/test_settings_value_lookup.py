import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils


class _MissQuery:
    def __init__(self, db):
        self._db = db

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _FallbackQuery:
    def __init__(self, db):
        self._db = db

    def yield_per(self, batch_size):
        self._db.yield_per_batch_size = batch_size
        return iter(self._db.users)


class _SettingsLookupDB:
    def __init__(self, users):
        self.users = users
        self.query_count = 0
        self.yield_per_batch_size = None

    def query(self, *args, **kwargs):
        self.query_count += 1
        if self.query_count == 1:
            return _MissQuery(self)
        return _FallbackQuery(self)


def test_find_user_by_settings_value_falls_back_after_empty_jsonb_query():
    matching_user = SimpleNamespace(
        id="user-1",
        settings={"ldap_login": {"directory_user_id": "directory-id"}},
    )
    db = _SettingsLookupDB(
        [
            SimpleNamespace(
                id="user-0",
                settings={"ldap_login": {"directory_user_id": "other-id"}},
            ),
            matching_user,
        ]
    )

    user = auth_utils._find_user_by_settings_value(
        db,
        ("ldap_login", "directory_user_id"),
        ["directory-id"],
        use_constant_time=True,
    )

    assert user is matching_user
    assert db.query_count == 2
    assert db.yield_per_batch_size == 200
