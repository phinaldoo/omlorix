from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda *writer_args, **writer_kwargs: SimpleNamespace()
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda *reader_args, **reader_kwargs: SimpleNamespace()
    )
    sys.modules["zstandard"] = fake_zstandard


from app.auth import token as auth_token


class _FakeColumn:
    """Small SQLAlchemy-column stand-in for the activity recorder unit tests."""

    def __init__(self, name: str):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)

    def __le__(self, value):
        return ("le", self.name, value)

    def __lt__(self, value):
        return ("lt", self.name, value)

    def is_(self, value):
        return ("is", self.name, value)

    def __hash__(self):
        return hash(self.name)


class _FakeUserModel:
    id = _FakeColumn("users.id")
    last_active_at = _FakeColumn("users.last_active_at")


class _FakeAuthenticationModel:
    id = _FakeColumn("authentication.id")
    last_active_at = _FakeColumn("authentication.last_active_at")


class _FakeQuery:
    """Minimal query object that applies the same stale-row condition as SQL."""

    def __init__(self, db: "_FakeDb", model):
        self.db = db
        self.model = model
        self.filters = ()

    def filter(self, *filters):
        self.filters = filters
        return self

    def update(self, values, synchronize_session=False):
        self.db.update_calls.append((self.model, self.filters, values, synchronize_session))
        activity_filter = next(filter_value for filter_value in self.filters if filter_value[0] == "or")
        comparison, _name, threshold = next(
            clause for clause in activity_filter[1] if clause[0] in {"lt", "le"}
        )
        current_time = next(iter(values.values()))

        if self.model is _FakeUserModel:
            user_is_due = self.db.user_last_active_at is None or (
                self.db.user_last_active_at < threshold
                if comparison == "lt"
                else self.db.user_last_active_at <= threshold
            )
            if user_is_due:
                self.db.user_last_active_at = current_time
                return 1
            return 0

        if self.db.auth_last_active_at is None or self.db.auth_last_active_at <= threshold:
            self.db.auth_last_active_at = current_time
            return 1
        return 0


class _FakeDb:
    """In-memory DB facade for exercising conditional update behavior."""

    def __init__(self, *, user_last_active_at: datetime, auth_last_active_at: datetime):
        self.user_last_active_at = user_last_active_at
        self.auth_last_active_at = auth_last_active_at
        self.update_calls = []
        self.commit_count = 0

    def query(self, model):
        return _FakeQuery(self, model)

    def commit(self):
        self.commit_count += 1


def _patch_activity_models(monkeypatch):
    monkeypatch.setattr(auth_token, "User", _FakeUserModel)
    monkeypatch.setattr(auth_token, "Authentication", _FakeAuthenticationModel)
    monkeypatch.setattr(auth_token, "or_", lambda *clauses: ("or", clauses))


def test_authenticated_activity_skips_recent_rows(monkeypatch):
    now = datetime(2026, 6, 16, 12, 3, tzinfo=timezone.utc)
    recent = now - timedelta(minutes=1)
    db = _FakeDb(user_last_active_at=recent, auth_last_active_at=recent)
    user = SimpleNamespace(id="user-1", last_active_at=recent)
    auth_entry = SimpleNamespace(id="auth-1", last_active_at=recent)
    presence_calls = []

    _patch_activity_models(monkeypatch)
    monkeypatch.setattr(
        auth_token,
        "record_user_activity_presence",
        lambda *args, **kwargs: presence_calls.append(args),
    )

    auth_token._record_authenticated_activity(db, user, auth_entry, now=now)

    assert db.update_calls == []
    assert db.commit_count == 0
    assert presence_calls == []
    assert user.last_active_at == recent
    assert auth_entry.last_active_at == recent


def test_authenticated_activity_updates_stale_rows_once(monkeypatch):
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    stale = now - timedelta(minutes=10)
    db = _FakeDb(user_last_active_at=stale, auth_last_active_at=stale)
    user = SimpleNamespace(id="user-1", last_active_at=stale)
    auth_entry = SimpleNamespace(id="auth-1", last_active_at=stale)
    presence_calls = []

    _patch_activity_models(monkeypatch)
    monkeypatch.setattr(
        auth_token,
        "record_user_activity_presence",
        lambda db_arg, user_arg, now=None: presence_calls.append((db_arg, user_arg, now)),
    )

    auth_token._record_authenticated_activity(db, user, auth_entry, now=now)

    assert len(db.update_calls) == 2
    assert db.commit_count == 1
    assert presence_calls == [(db, user, now)]
    assert db.user_last_active_at == now
    assert db.auth_last_active_at == now
    assert user.last_active_at == now
    assert auth_entry.last_active_at == now


def test_authenticated_activity_updates_null_activity_rows(monkeypatch):
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    db = _FakeDb(user_last_active_at=None, auth_last_active_at=None)
    user = SimpleNamespace(id="user-1", last_active_at=None)
    auth_entry = SimpleNamespace(id="auth-1", last_active_at=None)
    presence_calls = []

    _patch_activity_models(monkeypatch)
    monkeypatch.setattr(
        auth_token,
        "record_user_activity_presence",
        lambda db_arg, user_arg, now=None: presence_calls.append((db_arg, user_arg, now)),
    )

    auth_token._record_authenticated_activity(db, user, auth_entry, now=now)

    assert len(db.update_calls) == 2
    assert db.commit_count == 1
    assert presence_calls == [(db, user, now)]
    assert db.user_last_active_at == now
    assert db.auth_last_active_at == now
    assert user.last_active_at == now
    assert auth_entry.last_active_at == now


def test_authenticated_activity_skips_when_parallel_request_already_updated_database(monkeypatch):
    now = datetime(2026, 6, 16, 12, 3, tzinfo=timezone.utc)
    stale = now - timedelta(minutes=10)
    recent = now - timedelta(minutes=1)
    db = _FakeDb(user_last_active_at=recent, auth_last_active_at=recent)
    user = SimpleNamespace(id="user-1", last_active_at=stale)
    auth_entry = SimpleNamespace(id="auth-1", last_active_at=stale)
    presence_calls = []

    _patch_activity_models(monkeypatch)
    monkeypatch.setattr(
        auth_token,
        "record_user_activity_presence",
        lambda *args, **kwargs: presence_calls.append(args),
    )

    auth_token._record_authenticated_activity(db, user, auth_entry, now=now)

    assert len(db.update_calls) == 2
    assert db.commit_count == 0
    assert presence_calls == []
    assert db.user_last_active_at == recent
    assert db.auth_last_active_at == recent
    assert user.last_active_at == stale
    assert auth_entry.last_active_at == stale


def test_authenticated_activity_records_request_just_after_bucket_boundary(monkeypatch):
    """A boundary-crossing request must not be lost to the rolling write throttle."""

    now = datetime(2026, 6, 16, 12, 5, 1, tzinfo=timezone.utc)
    previous = datetime(2026, 6, 16, 12, 4, 59, tzinfo=timezone.utc)
    db = _FakeDb(user_last_active_at=previous, auth_last_active_at=previous)
    user = SimpleNamespace(id="user-1", last_active_at=previous)
    auth_entry = SimpleNamespace(id="auth-1", last_active_at=previous)
    presence_calls = []

    _patch_activity_models(monkeypatch)
    monkeypatch.setattr(
        auth_token,
        "record_user_activity_presence",
        lambda db_arg, user_arg, now=None: presence_calls.append((db_arg, user_arg, now)),
    )

    auth_token._record_authenticated_activity(db, user, auth_entry, now=now)

    # User presence advances once in the new bucket. The authentication session
    # remains throttled because only two seconds elapsed.
    assert len(db.update_calls) == 1
    assert db.commit_count == 1
    assert presence_calls == [(db, user, now)]
    assert user.last_active_at == now
    assert auth_entry.last_active_at == previous
