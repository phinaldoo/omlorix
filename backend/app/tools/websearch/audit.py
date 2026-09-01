from typing import Any


def build_aiohttp_tls_audit_details(provider_key: str, settings: dict[str, Any] | None) -> dict[str, Any]:
    if provider_key != "aiohttp" or settings is None:
        return {}

    verify_ssl = bool(settings.get("verify_ssl_certificate", True))
    details: dict[str, Any] = {
        "verify_ssl_certificate": verify_ssl,
    }
    if not verify_ssl:
        details["insecure_tls_opt_in"] = True
    return details


def build_import_aiohttp_tls_audit_details(payload: dict, result: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    data_block = payload.get("data") if isinstance(payload, dict) else None
    raw_providers = data_block.get("providers") if isinstance(data_block, dict) else None
    created = result.get("created") if isinstance(result, dict) else None
    if not isinstance(raw_providers, list) or not isinstance(created, list):
        return {}

    created_keys = {
        (
            str(item.get("provider") or "").strip().lower(),
            str(item.get("name") or "").strip(),
        )
        for item in created
        if isinstance(item, dict)
    }

    insecure_provider_names: list[str] = []
    for entry in raw_providers:
        if not isinstance(entry, dict):
            continue
        provider_key = str(entry.get("provider") or "").strip().lower()
        provider_name = str(entry.get("name") or "").strip()
        if provider_key != "aiohttp" or not provider_name:
            continue
        if (provider_key, provider_name) not in created_keys:
            continue
        settings = entry.get("settings")
        verify_ssl = True
        if isinstance(settings, dict):
            verify_ssl = bool(settings.get("verify_ssl_certificate", True))
        if not verify_ssl:
            insecure_provider_names.append(provider_name)

    if not insecure_provider_names:
        return {}

    return {
        "insecure_aiohttp_provider_count": len(insecure_provider_names),
        "insecure_aiohttp_provider_names": insecure_provider_names,
    }
