import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools.websearch.audit import (
    build_aiohttp_tls_audit_details,
    build_import_aiohttp_tls_audit_details,
)


def test_aiohttp_tls_audit_details_mark_insecure_admin_opt_in():
    details = build_aiohttp_tls_audit_details(
        "aiohttp",
        {"verify_ssl_certificate": False},
    )

    assert details == {
        "verify_ssl_certificate": False,
        "insecure_tls_opt_in": True,
    }


def test_import_aiohttp_tls_audit_details_only_include_created_insecure_providers():
    payload = {
        "data": {
            "providers": [
                {
                    "provider": "aiohttp",
                    "name": "Insecure Import",
                    "settings": {"verify_ssl_certificate": False},
                },
                {
                    "provider": "aiohttp",
                    "name": "Secure Import",
                    "settings": {"verify_ssl_certificate": True},
                },
                {
                    "provider": "aiohttp",
                    "name": "Failed Import",
                    "settings": {"verify_ssl_certificate": False},
                },
            ]
        }
    }
    result = {
        "created": [
            {"provider": "aiohttp", "name": "Insecure Import"},
            {"provider": "aiohttp", "name": "Secure Import"},
        ],
        "errors": [
            {"provider": "aiohttp", "name": "Failed Import"},
        ],
    }

    details = build_import_aiohttp_tls_audit_details(payload, result)

    assert details == {
        "insecure_aiohttp_provider_count": 1,
        "insecure_aiohttp_provider_names": ["Insecure Import"],
    }
