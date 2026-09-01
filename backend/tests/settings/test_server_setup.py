from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.modules.setdefault("zstandard", SimpleNamespace())

from app.settings.schemas import ServerSetupRequest


def test_server_setup_request_accepts_and_normalizes_multiple_public_urls():
    """The setup contract preserves order and supports old scalar clients."""
    payload = ServerSetupRequest(
        application_name="Omlorix",
        public_url=[
            "HTTPS://PRIMARY.EXAMPLE:443/path",
            "http://secondary.example:8080/admin",
            "https://primary.example/duplicate",
        ],
        default_user_role="pending",
    )
    legacy_payload = ServerSetupRequest(
        application_name="Omlorix",
        public_url="https://legacy.example/path",
        default_user_role="pending",
    )

    assert payload.public_url == [
        "https://primary.example",
        "http://secondary.example:8080",
    ]
    assert legacy_payload.public_url == ["https://legacy.example"]


def test_server_setup_request_has_no_legal_configuration_fields():
    """Legal links and review confirmation are configured outside server setup."""
    legal_field_names = {
        "show_privacy_notice_link",
        "show_terms_of_service_link",
        "legal_review_confirmed",
    }

    assert legal_field_names.isdisjoint(ServerSetupRequest.model_fields)
