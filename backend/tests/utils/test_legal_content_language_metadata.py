from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

dependencies_stub = types.ModuleType("app.dependencies")
dependencies_stub.get_db = lambda: None
dependencies_stub.verified_admin = lambda: None
sys.modules.setdefault("app.dependencies", dependencies_stub)


from app.utils import router as utils_router
from app.utils.terms_of_service_template import TERMS_OF_SERVICE_TEMPLATE


def _response_payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_privacy_endpoint_marks_default_template_as_english_authoritative(monkeypatch):
    monkeypatch.setattr(utils_router, "get_privacy_policy", lambda db: "# Privacy")
    monkeypatch.setattr(utils_router, "is_default_privacy_policy", lambda content: True)
    monkeypatch.setattr(
        utils_router,
        "get_privacy_policy_notice_policy",
        lambda db: {"revision": 4, "notice_updated_at": "2026-05-17T10:00:00+00:00"},
    )

    payload = _response_payload(utils_router.privacy(db=object()))

    assert payload["content_language"] == "en"
    assert payload["authoritative_language"] == "en"
    assert payload["localized_content_available"] is False
    assert payload["revision"] == 4
    assert payload["updated_at"] == "2026-05-17T10:00:00+00:00"


def test_privacy_endpoint_does_not_guess_custom_content_language(monkeypatch):
    monkeypatch.setattr(utils_router, "get_privacy_policy", lambda db: "# Datenschutz")
    monkeypatch.setattr(utils_router, "is_default_privacy_policy", lambda content: False)
    monkeypatch.setattr(
        utils_router,
        "get_privacy_policy_notice_policy",
        lambda db: {"revision": 2, "notice_updated_at": ""},
    )

    payload = _response_payload(utils_router.privacy(db=object()))

    assert payload["content_language"] is None
    assert payload["authoritative_language"] is None
    assert payload["localized_content_available"] is False


def test_terms_endpoint_marks_default_template_as_english_authoritative(monkeypatch):
    monkeypatch.setattr(utils_router, "get_terms_of_service", lambda db: TERMS_OF_SERVICE_TEMPLATE)
    monkeypatch.setattr(
        utils_router,
        "get_terms_of_service_policy",
        lambda db: {
            "revision": 3,
            "updated_at": "2026-05-20T09:30:00+00:00",
            "show_link_on_login": False,
            "signup_available": False,
            "signup_block_reason": "terms_configuration_required",
            "is_default_template": True,
            "customization_required": True,
        },
    )

    payload = _response_payload(utils_router.terms(db=object()))

    assert payload["content_language"] == "en"
    assert payload["authoritative_language"] == "en"
    assert payload["localized_content_available"] is False
    assert payload["revision"] == 3
    assert payload["updated_at"] == "2026-05-20T09:30:00+00:00"
    assert payload["signup_available"] is False
    assert payload["signup_block_reason"] == "terms_configuration_required"
