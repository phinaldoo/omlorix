"""OpenAI provider, model-listing, and model-creation operations.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import utils as _compat_source

_COMPAT_DEPENDENCIES = {
    "create_openai_provider": (
        "HTTPException",
        "allows_manual_openai_model_entry",
        "create_llm_provider",
        "datetime",
        "list_models_openai",
        "logger",
        "timezone",
    ),
    "list_models_openai": (
        "APIConnectionError",
        "AuthenticationError",
        "BadRequestError",
        "HTTPException",
        "OpenAI",
        "XAI_PROVIDER_TYPE",
        "_merge_openai_request_options",
        "_parse_openai_exception",
        "_resolve_openai_client_context",
        "get_responses_unsupported_models",
        "logger",
        "normalize_openai_provider_type",
    ),
    "openai_create_model": (
        "HTTPException",
        "Models",
        "ProviderEnum",
        "allows_manual_openai_model_entry",
        "datetime",
        "determine_model_capabilities",
        "get_llm_provider",
        "jsonable_encoder",
        "list_models_openai",
        "logger",
        "timezone",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "APIConnectionError",
    "AuthenticationError",
    "BadRequestError",
    "HTTPException",
    "Models",
    "OpenAI",
    "ProviderEnum",
    "XAI_PROVIDER_TYPE",
    "_merge_openai_request_options",
    "_parse_openai_exception",
    "_resolve_openai_client_context",
    "allows_manual_openai_model_entry",
    "create_llm_provider",
    "datetime",
    "determine_model_capabilities",
    "get_llm_provider",
    "get_responses_unsupported_models",
    "jsonable_encoder",
    "list_models_openai",
    "logger",
    "normalize_openai_provider_type",
    "timezone",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_create_openai_provider(
    db,
    name: str,
    api_key: str,
    settings: dict,
    icon: str | None = None,
    openai_provider_type: str = "openai",
):
    """Create an OpenAI provider."""
    status = {
        "available": "unknown",
        "model_list": [],
        "supports_model_list": True,
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }

    byok_creds = {
        "api_key": api_key,
        "base_url": settings.get("base_url"),
        "azure_endpoint": settings.get("azure_endpoint"),
        "api_version": settings.get("api_version"),
        "organization": settings.get("organization"),
        "project": settings.get("project"),
        "custom_headers": settings.get("custom_headers"),
    }

    try:
        models = list_models_openai(
            db, byok=byok_creds, openai_provider_type=openai_provider_type
        )
        identifiers = {
            str(model.get("id") or model.get("model")).strip()
            for model in models
            if isinstance(model, dict) and str(model.get("id") or model.get("model"))
        }
        status["available"] = "up"
        status["model_list"] = sorted(
            identifier for identifier in identifiers if identifier
        )
    except HTTPException:
        if not allows_manual_openai_model_entry(openai_provider_type):
            status["available"] = "down"
        else:
            status["supports_model_list"] = False
    except Exception:
        if not allows_manual_openai_model_entry(openai_provider_type):
            status["available"] = "down"
            logger.exception("[OpenAI Provider] Failed to seed status during creation")
        else:
            status["supports_model_list"] = False
    return create_llm_provider(
        db, openai_provider_type, name, api_key, settings, status=status, icon=icon
    )


def _impl_list_models_openai(
    db: Session,
    openai_provider_id: str | None = None,
    byok: dict | None = None,
    openai_provider_type: str | None = "openai",
) -> list[dict]:
    """List models from an OpenAI Responses-compatible provider."""
    client_context = _resolve_openai_client_context(
        db, openai_provider_id, byok, openai_provider_type or "openai"
    )
    client_kwargs = client_context["client_kwargs"]
    request_options = client_context["request_options"]
    client = OpenAI(**client_kwargs)
    try:
        models = client.models.list(
            **_merge_openai_request_options(request_options=request_options)
        )
    except (AuthenticationError, BadRequestError, APIConnectionError) as exc:
        status, message, _, _ = _parse_openai_exception(exc)
        if not isinstance(status, int) or not 400 <= status <= 599:
            # Connection failures do not carry an upstream HTTP response, so
            # the SDK exposes ``status_code=None``. Starlette requires a real
            # integer here; otherwise the provider error is replaced by a
            # secondary TypeError while constructing the response.
            status = 502 if isinstance(exc, APIConnectionError) else 424
        raise HTTPException(
            status_code=status, detail=f"Failed to list OpenAI models: {message}"
        )
    except Exception as exc:
        logger.exception("Failed to list OpenAI models")
        raise HTTPException(
            status_code=424, detail=f"Failed to list OpenAI models: {exc}"
        )

    unsupported_ids = get_responses_unsupported_models(openai_provider_type)
    items = []
    seen_identifiers: set[str] = set()
    canonical_identifiers: set[str] = set()
    pending_aliases: list[tuple[str, str, dict]] = []
    is_xai_provider = (
        normalize_openai_provider_type(openai_provider_type) == XAI_PROVIDER_TYPE
    )
    for model in models:
        if model.id in unsupported_ids:
            continue
        model_payload = (
            model.model_dump()
            if hasattr(model, "model_dump") and callable(model.model_dump)
            else {}
        )
        model_extra = getattr(model, "model_extra", None)
        if isinstance(model_extra, dict):
            model_payload.update(model_extra)

        base_item = {
            "id": model.id,
            "created": getattr(model, "created", None),
            "object": getattr(model, "object", "model"),
            "owned_by": getattr(model, "owned_by", None),
        }
        # xAI's model-list contract includes useful live metadata.  Preserve
        # it for callers that can consume richer model rows without changing
        # the stable four-field surface returned for OpenAI itself.
        if is_xai_provider:
            for key in (
                "context_length",
                "prompt_text_token_price",
                "cached_prompt_text_token_price",
                "prompt_image_token_price",
                "completion_text_token_price",
            ):
                if model_payload.get(key) is not None:
                    base_item[key] = model_payload[key]

        if model.id not in seen_identifiers:
            items.append(base_item)
            seen_identifiers.add(model.id)
            canonical_identifiers.add(model.id)

        # The xAI API returns aliases alongside each canonical model.  Treat
        # them as selectable IDs because requests may legitimately use either.
        if is_xai_provider:
            aliases = (
                model_payload.get("aliases") or getattr(model, "aliases", None) or []
            )
            for alias in aliases:
                alias_id = str(alias or "").strip()
                if not alias_id or alias_id in unsupported_ids:
                    continue
                pending_aliases.append((alias_id, model.id, base_item))

    # xAI aliases can collide with a canonical model returned later by the
    # provider. Emit aliases only after every canonical identifier is known so
    # the canonical row always keeps its own live metadata.
    for alias_id, canonical_id, base_item in pending_aliases:
        if alias_id in canonical_identifiers or alias_id in seen_identifiers:
            continue
        items.append({**base_item, "id": alias_id, "canonical_id": canonical_id})
        seen_identifiers.add(alias_id)
    return items


def _impl_openai_create_model(
    openai_provider_id: str,
    model: str,
    name: str,
    description: str,
    model_icon: str,
    settings: OpenAIModelSettings,
    tools,
    access,
    status: str,
    db: Session,
    openai_provider_type: str | None = "openai",
    save_model: bool | None = True,
    group_provider_id: str | None = None,
) -> Models | bool:
    """Create an OpenAI model.

    Args:
        openai_provider_id: The OpenAI provider ID.
        model: The model identifier.
        name: Display name for the model.
        description: Model description.
        model_icon: Icon for the model.
        settings: OpenAI model settings.
        tools: Tools configuration for the model.
        access: Access control configuration.
        status: Model status.
        db: Database session.
        openai_provider_type: The OpenAI provider type (default: "openai").
        save_model: Whether to save the model to database (default: True).
        group_provider_id: Optional group provider ID.

    Returns:
        Models: The created model instance when save_model is True.
        bool: True when save_model is False and validation succeeds.

    Raises:
        HTTPException: If model validation fails or creation encounters an error.
    """
    is_special_provider = allows_manual_openai_model_entry(openai_provider_type)

    provider_supports_model_list = True
    if openai_provider_id:
        try:
            provider = get_llm_provider(db, openai_provider_id)
        except HTTPException:
            provider = None
        if provider and isinstance(provider.status, dict):
            provider_supports_model_list = provider.status.get(
                "supports_model_list", True
            )

    should_enforce_model_validation = not is_special_provider
    attempt_model_listing = (
        should_enforce_model_validation or provider_supports_model_list
    )

    models: list[dict] = []
    has_model_data = False
    if attempt_model_listing:
        try:
            models = list_models_openai(
                db,
                openai_provider_id=openai_provider_id,
                openai_provider_type=openai_provider_type,
            )
            has_model_data = len(models) > 0
        except HTTPException:
            if should_enforce_model_validation:
                raise
            logger.warning(
                "Skipping OpenAI model listing for provider %s (%s) due to unsupported endpoint",
                openai_provider_id,
                openai_provider_type,
            )
            models = []
        except Exception as exc:
            if should_enforce_model_validation:
                logger.exception("Failed to list OpenAI models")
                raise HTTPException(
                    status_code=424, detail=f"Failed to list OpenAI models: {exc}"
                ) from exc
            logger.warning(
                "Skipping OpenAI model listing for provider %s (%s) due to unexpected error: %s",
                openai_provider_id,
                openai_provider_type,
                exc,
            )
            models = []

    if should_enforce_model_validation or (has_model_data and is_special_provider):
        supported = next(
            (m for m in models if m.get("model") == model or m.get("id") == model),
            None,
        )
        if not supported:
            raise HTTPException(
                status_code=400, detail="Model is not supported by this provider"
            )

    settings_payload = jsonable_encoder(settings)
    if not isinstance(settings_payload, dict):
        settings_payload = {}

    provider_slug = openai_provider_type or "openai"
    try:
        provider_enum = ProviderEnum(provider_slug)
    except ValueError:
        provider_enum = ProviderEnum.openai

    tools_payload = tools or []
    capabilities = determine_model_capabilities(
        provider_enum,
        settings_payload,
        tools_payload,
    )
    capabilities_unique = capabilities or ["completion"]

    try:
        if save_model:
            model_db = Models(
                name=name,
                description=description,
                model_icon=model_icon,
                provider=openai_provider_type,
                provider_id=openai_provider_id
                if not group_provider_id
                else group_provider_id,
                model_name=model,
                settings=jsonable_encoder(settings),
                capabilities=capabilities_unique,
                tools=jsonable_encoder(tools),
                access=jsonable_encoder(access),
                status=status,
                is_active=True,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            db.add(model_db)
            db.commit()
            db.refresh(model_db)
            return model_db
        else:
            return True
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create model: {exc}")
