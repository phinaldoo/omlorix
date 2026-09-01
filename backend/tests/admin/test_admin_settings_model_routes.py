"""Tests for typed, read-only admin settings model-option routes."""

import pytest

from app.admin.settings import router as settings_router
from app.admin.users import router as users_router


MODEL_OPTION_ROUTES = (
    (
        "admin_list_transcription_models",
        "get_transcription_model_options_response",
        "/api/v1/admin/settings/dictation/transcription/models",
    ),
    (
        "admin_list_live_transcription_models",
        "get_live_transcription_model_options_response",
        "/api/v1/admin/settings/dictation/live-transcription/models",
    ),
    (
        "admin_list_realtime_models",
        "get_realtime_model_options_response",
        "/api/v1/admin/settings/realtime/models",
    ),
)


def test_model_option_routes_are_owned_by_admin_settings_router():
    """Keep settings endpoints out of the user-management feature router."""
    settings_routes = {
        (route.path, method)
        for route in settings_router.admin_router.routes
        for method in route.methods or []
    }
    user_routes = {
        (route.path, method)
        for route in users_router.admin_router.routes
        for method in route.methods or []
    }

    expected_routes = {(path, "GET") for _, _, path in MODEL_OPTION_ROUTES}

    assert expected_routes <= settings_routes
    assert expected_routes.isdisjoint(user_routes)


@pytest.mark.parametrize(
    ("route_name", "helper_name", "_path"),
    MODEL_OPTION_ROUTES,
)
def test_model_option_routes_delegate_without_audit_writes(
    monkeypatch,
    route_name,
    helper_name,
    _path,
):
    """Repeated selector reads delegate without growing the audit log."""
    db = object()
    expected_result = {
        "provider_id": "provider-1",
        "options": [{"value": "model-1", "label": "Model 1"}],
    }
    helper_calls = []

    def fake_model_options_helper(*, db, provider_id):
        helper_calls.append({"db": db, "provider_id": provider_id})
        return expected_result

    monkeypatch.setattr(settings_router, helper_name, fake_model_options_helper)

    result = getattr(settings_router, route_name)(
        provider_id="provider-1",
        db=db,
    )

    assert result == expected_result
    assert helper_calls == [{"db": db, "provider_id": "provider-1"}]


def test_model_option_routes_declare_the_shared_response_model():
    """All three selectors expose one explicit FastAPI response contract."""

    from app.admin.settings.schema_categories.admin import AdminModelOptionsResponse

    route_by_path = {
        route.path: route
        for route in settings_router.admin_router.routes
    }
    for _route_name, _helper_name, path in MODEL_OPTION_ROUTES:
        assert route_by_path[path].response_model is AdminModelOptionsResponse
