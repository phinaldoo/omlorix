import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


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

from app.userNotifications import router as user_notifications_router


def test_listing_notifications_does_not_clear_new_notifications_flag(monkeypatch):
    monkeypatch.setattr(
        user_notifications_router,
        "get_user_notifications",
        lambda **kwargs: ([], 0),
    )
    monkeypatch.setattr(
        user_notifications_router,
        "clear_user_new_notifications_flag",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET list route must not mutate notification state")
        ),
    )

    response = user_notifications_router.list_user_notifications_route(
        page=1,
        page_size=20,
        db=object(),
        user=SimpleNamespace(id="user-1", group_id="group-1"),
    )

    assert response.notifications == []
    assert response.total == 0


def test_mark_seen_route_clears_new_notifications_flag(monkeypatch):
    calls = []

    monkeypatch.setattr(
        user_notifications_router,
        "clear_user_new_notifications_flag",
        lambda db, user_id: calls.append({"db": db, "user_id": user_id}),
    )

    db = object()
    result = user_notifications_router.mark_user_notifications_seen_route(
        db=db,
        user=SimpleNamespace(id="user-1"),
    )

    assert result is None
    assert calls == [{"db": db, "user_id": "user-1"}]
