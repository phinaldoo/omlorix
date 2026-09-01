"""Tests for the public login bootstrap settings endpoint."""

from app.settings import router as settings_router


def test_login_setup_is_available_without_origin_or_referer_headers(monkeypatch):
    """Privacy policies must not prevent the login page from bootstrapping."""
    calls: list[object] = []
    db = object()

    monkeypatch.setattr(
        settings_router,
        "get_login_settings",
        lambda current_db: calls.append(current_db) or {"enable_signin": True},
    )

    response = settings_router.get_login_settings_route(db)

    assert response == {"enable_signin": True}
    assert calls == [db]
