import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(stream_writer=lambda stream: stream)
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(stream_reader=lambda stream: stream)
    sys.modules["zstandard"] = fake_zstandard

from app.tools.websearch.models import (  # noqa: E402
    current_websearch_provider_export_version,
    export_websearch_providers,
    import_websearch_providers,
)


def _provider(**overrides):
    data = {
        "id": "provider-1",
        "provider": "serper",
        "name": "Serper",
        "settings": {
            "api_key": "serper-live-secret",
            "fallback_language": "en",
            "fallback_country": "US",
        },
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _provider_import_payload(provider_entry):
    return {
        "export_type": "websearch_provider",
        "export_version": current_websearch_provider_export_version,
        "data": {
            "providers": [provider_entry],
        },
    }


def test_websearch_provider_export_omits_live_api_keys():
    db = MagicMock()
    db.query.return_value.all.return_value = [_provider()]

    result = export_websearch_providers(db)

    exported_provider = result["data"]["providers"][0]
    assert result["export_version"] == current_websearch_provider_export_version
    assert "api_key" not in exported_provider["settings"]
    assert exported_provider["credentials"] == {
        "api_key_exported": False,
        "api_key_required": True,
        "api_key_configured": True,
    }
    assert "serper-live-secret" not in str(result)


def test_websearch_provider_export_marks_providers_without_api_key_requirement():
    db = MagicMock()
    db.query.return_value.all.return_value = [
        _provider(
            provider="duckduckgo",
            name="DuckDuckGo",
            settings={"fallback_language": "en", "fallback_country": "US"},
        )
    ]

    result = export_websearch_providers(db)

    exported_provider = result["data"]["providers"][0]
    assert exported_provider["credentials"]["api_key_exported"] is False
    assert exported_provider["credentials"]["api_key_required"] is False
    assert exported_provider["credentials"]["api_key_configured"] is False


def test_perplexity_export_uses_canonical_domain_policy_fields():
    """Backups expose the same allow/block representation used by other providers."""

    db = MagicMock()
    db.query.return_value.all.return_value = [
        _provider(
            provider="perplexity",
            name="Perplexity",
            settings={
                "api_key": "perplexity-live-secret",
                "allowed_domains": ["docs.example"],
                "blocked_domains": [],
            },
        )
    ]

    result = export_websearch_providers(db)

    exported_settings = result["data"]["providers"][0]["settings"]
    assert exported_settings == {
        "allowed_domains": ["docs.example"],
        "blocked_domains": [],
    }
    assert "search_domain_filter" not in exported_settings


def test_websearch_provider_export_omits_optional_crawl4ai_token():
    """Optional Crawl4AI credentials receive the same export redaction as API keys."""

    db = MagicMock()
    db.query.return_value.all.return_value = [
        _provider(
            provider="crawl4ai",
            name="Crawl4AI",
            settings={
                "api_key": "crawl4ai-live-secret",
                "base_url": "https://crawl4ai.example",
            },
        )
    ]

    result = export_websearch_providers(db)

    exported_provider = result["data"]["providers"][0]
    assert exported_provider["settings"] == {
        "base_url": "https://crawl4ai.example"
    }
    assert exported_provider["credentials"] == {
        "api_key_exported": False,
        "api_key_required": False,
        "api_key_configured": True,
    }
    assert "crawl4ai-live-secret" not in str(result)


def test_firecrawl_export_preserves_supported_proxy_modes_without_url_policy():
    """Export does not apply a base-URL-specific proxy compatibility rule."""

    db = MagicMock()
    db.query.return_value.all.return_value = [
        _provider(
            id="firecrawl-hosted",
            provider="firecrawl",
            name="Firecrawl Hosted",
            settings={
                "api_key": "hosted-secret",
                "base_url": "https://api.firecrawl.dev",
                "proxy": "enhanced",
            },
        ),
        _provider(
            id="firecrawl-custom",
            provider="firecrawl",
            name="Firecrawl Custom",
            settings={
                "api_key": "custom-secret",
                "base_url": "https://firecrawl.example",
                "proxy": "enhanced",
            },
        ),
    ]

    result = export_websearch_providers(db)
    exported = {
        provider["id"]: provider["settings"]
        for provider in result["data"]["providers"]
    }

    assert exported["firecrawl-hosted"]["proxy"] == "enhanced"
    assert exported["firecrawl-custom"]["proxy"] == "enhanced"
    assert "hosted-secret" not in str(result)
    assert "custom-secret" not in str(result)


def test_import_required_websearch_key_provider_fails_without_api_key():
    db = MagicMock()
    payload = _provider_import_payload(
        {
            "provider": "serper",
            "name": "Serper Import",
            "settings": {
                "fallback_language": "en",
                "fallback_country": "US",
            },
        }
    )

    result = import_websearch_providers(db, payload)

    assert result["created"] == []
    assert result["errors"] == [
        {
            "index": 0,
            "name": "Serper Import",
            "error": "Provider api_key is required.",
        }
    ]


def test_import_websearch_provider_succeeds_when_request_supplies_fresh_api_key():
    db = MagicMock()
    payload = _provider_import_payload(
        {
            "provider": "serper",
            "name": "Serper Import",
            "settings": {
                "api_key": "serper-new-secret",
                "fallback_language": "en",
                "fallback_country": "US",
            },
        }
    )
    created_provider = SimpleNamespace(id="provider-created", name="Serper Import", provider="serper")

    with patch("app.tools.websearch.models.create_websearch_provider", return_value=created_provider) as mock_create:
        result = import_websearch_providers(db, payload)

    mock_create.assert_called_once()
    assert mock_create.call_args.args[3]["api_key"] == "serper-new-secret"
    assert result == {
        "created": [
            {
                "id": "provider-created",
                "name": "Serper Import",
                "provider": "serper",
            }
        ],
        "errors": [],
    }


def test_import_perplexity_preserves_canonical_domain_policy():
    """Canonical Perplexity rules are normalized and persisted during import."""

    db = MagicMock()
    payload = _provider_import_payload(
        {
            "provider": "perplexity",
            "name": "Perplexity Import",
            "settings": {
                "api_key": "perplexity-new-secret",
                "blocked_domains": [" Blocked.Example ", "blocked.example"],
            },
        }
    )
    created_provider = SimpleNamespace(
        id="provider-created",
        name="Perplexity Import",
        provider="perplexity",
    )

    with patch(
        "app.tools.websearch.models.create_websearch_provider",
        return_value=created_provider,
    ) as mock_create:
        result = import_websearch_providers(db, payload)

    imported_settings = mock_create.call_args.args[3]
    assert imported_settings["blocked_domains"] == ["blocked.example"]
    assert imported_settings.get("allowed_domains") is None
    assert "search_domain_filter" not in imported_settings
    assert result["errors"] == []


def test_import_exa_preserves_locale_controls_and_normalizes_neural_type():
    """Exa's persisted locale controls survive import while neural migrates to auto."""

    db = MagicMock()
    payload = _provider_import_payload(
        {
            "provider": "exa",
            "name": "Exa Import",
            "settings": {
                "api_key": "exa-new-secret",
                "fallback_country": "DE",
                "forward_user_locale": True,
                "type": "neural",
            },
        }
    )
    created_provider = SimpleNamespace(
        id="provider-created",
        name="Exa Import",
        provider="exa",
    )

    with patch(
        "app.tools.websearch.models.create_websearch_provider",
        return_value=created_provider,
    ) as mock_create:
        result = import_websearch_providers(db, payload)

    imported_settings = mock_create.call_args.args[3]
    assert imported_settings["api_key"] == "exa-new-secret"
    assert imported_settings["fallback_country"] == "DE"
    assert imported_settings["forward_user_locale"] is True
    assert imported_settings["type"] == "auto"
    assert result["errors"] == []


def test_websearch_provider_export_route_writes_metadata_only_audit_log():
    from app.tools.websearch import router as websearch_router_module

    db = MagicMock()
    db.query.return_value.all.return_value = [
        _provider(),
        _provider(
            id="provider-2",
            provider="duckduckgo",
            name="DuckDuckGo",
            settings={"fallback_language": "en", "fallback_country": "US"},
        ),
    ]
    db_log = MagicMock()
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})
    admin_user = SimpleNamespace(id="admin-1")

    with patch.object(websearch_router_module, "create_audit_log") as mock_audit:
        result = websearch_router_module.export_websearch_providers_route(request, db, db_log, admin_user)

    mock_audit.assert_called_once()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["db_log"] is db_log
    assert audit_kwargs["user_id"] == "admin-1"
    assert audit_kwargs["action"] == "EXPORT_WEBSEARCH_PROVIDERS"
    assert audit_kwargs["category"] == "websearch_provider"
    assert audit_kwargs["details"] == {
        "export_version": current_websearch_provider_export_version,
        "provider_count": 2,
        "provider_types": ["duckduckgo", "serper"],
        "required_api_key_count": 1,
        "configured_api_key_count": 1,
        "api_keys_exported": False,
    }
    assert "serper-live-secret" not in str(audit_kwargs)
    assert "serper-live-secret" not in str(result)


def test_create_websearch_provider_route_redacts_api_key_in_response():
    from app.tools.websearch import router as websearch_router_module

    db = MagicMock()
    db_log = MagicMock()
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})
    admin_user = SimpleNamespace(id="admin-1")
    payload = SimpleNamespace(
        provider="serper",
        name="Serper",
        settings={
            "api_key": "serper-create-secret",
            "fallback_language": "en",
            "fallback_country": "US",
            "num_results": 5,
        },
    )
    created_provider = _provider(
        settings={
            "api_key": "serper-create-secret",
            "fallback_language": "en",
            "fallback_country": "US",
            "num_results": 5,
        }
    )

    with patch.object(websearch_router_module, "create_websearch_provider", return_value=created_provider), patch.object(
        websearch_router_module, "create_audit_log"
    ):
        result = websearch_router_module.create_websearch_provider_route(payload, request, db, db_log, admin_user)

    response = result.model_dump()
    assert response["settings"] == {
        "fallback_language": "en",
        "fallback_country": "US",
        "forward_user_locale": False,
        "num_results": 5,
    }
    assert "api_key" not in response["settings"]
    assert "serper-create-secret" not in str(response)


def test_update_websearch_provider_route_redacts_api_key_in_response():
    from app.tools.websearch import router as websearch_router_module

    existing_provider = _provider(
        settings={
            "api_key": "serper-existing-secret",
            "fallback_language": "en",
            "fallback_country": "US",
            "num_results": 5,
        }
    )
    updated_provider = _provider(
        name="Serper Updated",
        settings={
            "api_key": "serper-updated-secret",
            "fallback_language": "de",
            "fallback_country": "DE",
            "num_results": 3,
        },
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = existing_provider
    db_log = MagicMock()
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})
    admin_user = SimpleNamespace(id="admin-1")
    payload = SimpleNamespace(
        name="Serper Updated",
        settings={
            "api_key": "serper-updated-secret",
            "fallback_language": "de",
            "fallback_country": "DE",
            "num_results": 3,
        },
        model_fields_set={"name", "settings"},
    )

    with patch.object(websearch_router_module, "update_websearch_provider", return_value=updated_provider), patch.object(
        websearch_router_module, "create_audit_log"
    ):
        result = websearch_router_module.update_websearch_provider_route(
            "provider-1",
            payload,
            request,
            db,
            db_log,
            admin_user,
        )

    response = result.model_dump()
    assert response["name"] == "Serper Updated"
    assert response["settings"] == {
        "fallback_language": "de",
        "fallback_country": "DE",
        "forward_user_locale": False,
        "num_results": 3,
    }
    assert "api_key" not in response["settings"]
    assert "serper-updated-secret" not in str(response)
