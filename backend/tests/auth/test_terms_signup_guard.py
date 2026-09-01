import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils


def test_terms_helper_requires_acceptance_even_without_signup_availability(monkeypatch):
    monkeypatch.setattr(
        auth_utils,
        "get_terms_of_service_policy",
        lambda db: {
            "revision": 4,
            "signup_available": False,
            "require_current_revision_for_signup": True,
        },
    )

    try:
        auth_utils._require_terms_ready_for_self_service_signup(object(), {})
    except auth_utils.TermsOfServiceSignupError as exc:
        assert exc.code == "terms_acceptance_required"
        assert exc.status_code == 400
        assert exc.revision == 4
    else:
        raise AssertionError("Expected TermsOfServiceSignupError")


def test_terms_helper_requires_explicit_acceptance(monkeypatch):
    monkeypatch.setattr(
        auth_utils,
        "get_terms_of_service_policy",
        lambda db: {
            "revision": 7,
            "signup_available": True,
            "require_current_revision_for_signup": True,
        },
    )

    try:
        auth_utils._require_terms_ready_for_self_service_signup(object(), {"accept_terms_of_service": False})
    except auth_utils.TermsOfServiceSignupError as exc:
        assert exc.code == "terms_acceptance_required"
        assert exc.status_code == 400
        assert exc.revision == 7
    else:
        raise AssertionError("Expected TermsOfServiceSignupError")


def test_terms_helper_rejects_stale_revision(monkeypatch):
    monkeypatch.setattr(
        auth_utils,
        "get_terms_of_service_policy",
        lambda db: {
            "revision": 9,
            "signup_available": True,
            "require_current_revision_for_signup": True,
        },
    )

    try:
        auth_utils._require_terms_ready_for_self_service_signup(
            object(),
            {"accept_terms_of_service": True, "terms_of_service_revision": 8},
        )
    except auth_utils.TermsOfServiceSignupError as exc:
        assert exc.code == "terms_revision_mismatch"
        assert exc.status_code == 409
        assert exc.revision == 9
    else:
        raise AssertionError("Expected TermsOfServiceSignupError")


def test_terms_helper_skips_signup_acceptance_when_toggle_is_disabled(monkeypatch):
    monkeypatch.setattr(
        auth_utils,
        "get_terms_of_service_policy",
        lambda db: {
            "revision": 9,
            "signup_available": False,
            "require_current_revision_for_signup": False,
        },
    )

    result = auth_utils._require_terms_ready_for_self_service_signup(object(), {})

    assert result == {}


def test_signup_returns_machine_readable_terms_error(monkeypatch):
    monkeypatch.setattr(auth_utils, "_client_ip_from_request", lambda *_args, **_kwargs: "203.0.113.10")
    monkeypatch.setattr(auth_utils, "check_blocked_ip_address", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(auth_utils, "_is_new_account_registration_enabled", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *_args, **_kwargs: None)

    def raise_terms_error(_db, _user):
        raise auth_utils.TermsOfServiceSignupError(
            "terms_acceptance_required",
            "accept current terms",
            status_code=400,
            revision=5,
        )

    monkeypatch.setattr(auth_utils, "_require_terms_ready_for_self_service_signup", raise_terms_error)

    result = auth_utils.signup(
        db=object(),
        db_log=object(),
        request=SimpleNamespace(headers={"User-Agent": "pytest"}),
        user=SimpleNamespace(),
    )

    assert result == {
        "status": "termsAcceptanceRequired",
        "detail": "accept current terms",
        "revision": 5,
    }
