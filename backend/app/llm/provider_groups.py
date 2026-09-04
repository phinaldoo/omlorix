"""
Provider Groups - Load balancing and model intersection logic for provider groups.

This module provides:
- Weighted random selection of providers from a group
- Health-aware failover when a provider is unavailable
- Model intersection to find common models across all providers in a group
"""
import json
import random
import logging
from typing import Any

from fastapi import HTTPException

from app.llm.models import (
    LLMProviderGroup,
    LLMProvider,
    get_provider_group,
    get_llm_provider,
)
from app.llm.schemas import resolve_provider_icon


logger = logging.getLogger(__name__)


def _get_provider_availability(provider: LLMProvider) -> str:
    """Get the availability status of a provider."""
    status = provider.status if isinstance(provider.status, dict) else {}
    return status.get("available", "unknown")


def _is_provider_healthy(provider: LLMProvider) -> bool:
    """Check if a provider is considered healthy (not down)."""
    availability = _get_provider_availability(provider)
    return availability != "down"


def select_provider_from_group(db, group_id: str, exclude_ids: list[str] | None = None) -> LLMProvider:
    """
    Select a provider from a group using weighted random selection.
    
    Args:
        db: Database session
        group_id: ID of the provider group
        exclude_ids: Optional list of provider IDs to exclude (for failover)
    
    Returns:
        Selected LLMProvider instance
    
    Raises:
        HTTPException: If no healthy providers are available
    """
    group = get_provider_group(db, group_id)
    members = group.members or []
    exclude_set = set(exclude_ids or [])
    
    # Build list of healthy providers with weights
    candidates = []
    for member in members:
        provider_id = member.get("provider_id")
        weight = member.get("weight", 1)
        
        if provider_id in exclude_set:
            continue
        
        try:
            provider = get_llm_provider(db, provider_id)
        except HTTPException:
            logger.warning("Provider %s in group %s not found, skipping", provider_id, group_id)
            continue
        
        if _is_provider_healthy(provider):
            candidates.append((provider, weight))
        else:
            logger.info("Provider %s is down, skipping for group %s", provider_id, group_id)
    
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail="No healthy providers available in this group"
        )
    
    # Weighted random selection
    total_weight = sum(w for _, w in candidates)
    pick = random.uniform(0, total_weight)
    
    current = 0
    for provider, weight in candidates:
        current += weight
        if pick <= current:
            return provider
    
    # Fallback to last candidate (shouldn't happen)
    return candidates[-1][0]


def get_group_member_providers(db, group_id: str) -> list[LLMProvider]:
    """
    Get all provider instances for a group's members.
    
    Args:
        db: Database session
        group_id: ID of the provider group
    
    Returns:
        List of LLMProvider instances
    """
    group = get_provider_group(db, group_id)
    members = group.members or []
    
    providers = []
    for member in members:
        provider_id = member.get("provider_id")
        try:
            provider = get_llm_provider(db, provider_id)
            providers.append(provider)
        except HTTPException:
            logger.warning("Provider %s in group %s not found", provider_id, group_id)
    
    return providers


def get_group_common_models(db, group_id: str) -> list[dict]:
    """
    Get models that are common across ALL providers in a group.
    
    This intersects the model lists from all member providers and returns
    only models that are supported by every provider in the group.
    
    Args:
        db: Database session
        group_id: ID of the provider group
    
    Returns:
        List of common model dictionaries with id, model, name, description
    """
    # Provider selection is also used by standalone workers. Importing the
    # model-discovery facade eagerly creates a cycle back into this module.
    from app.llm.utils import list_provider_models

    group = get_provider_group(db, group_id)
    members = group.members or []
    providers_by_id: dict[str, LLMProvider] = {}
    group_provider_type: str | None = None

    if len(members) < 2:
        raise HTTPException(
            status_code=400,
            detail="Provider group must have at least 2 members"
        )

    for member in members:
        provider_id = member.get("provider_id")
        if not provider_id:
            raise HTTPException(status_code=400, detail="Invalid provider_id in group member")
        provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
        if not provider:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
        providers_by_id[provider_id] = provider
        if group_provider_type is None:
            group_provider_type = provider.provider

    # Collect model lists from each provider
    model_sets: list[dict[str, dict]] = []

    for member in members:
        provider_id = member.get("provider_id")
        provider_obj = providers_by_id.get(provider_id)
        provider_type = (provider_obj.provider if provider_obj else "") or ""
        try:
            models = list_provider_models(db, provider_id)
            # Index by model identifier (the actual model name/id used for API calls)
            model_dict = {}
            for m in models:
                # Use 'model' field as the key (this is the actual model identifier)
                model_key = m.get("model") or m.get("id") or ""
                if model_key:
                    model_dict[model_key] = m
            model_sets.append(model_dict)
        except HTTPException as exc:
            logger.warning("Failed to list models for provider %s: %s", provider_id, exc)
            if provider_type == "ollama":
                detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=f"Failed to list models for provider '{provider_id}': {detail}",
                ) from exc
            model_sets.append({})
        except Exception as e:
            logger.warning("Failed to list models for provider %s: %s", provider_id, e)
            if provider_type == "ollama":
                raise HTTPException(status_code=424, detail=f"Provider '{provider_id}' is unavailable: {e}") from e
            # If we can't get models from a provider, treat it as empty set
            model_sets.append({})
    
    if not model_sets:
        return []
    
    # Find intersection of model keys across all providers
    common_keys = set(model_sets[0].keys())
    for model_dict in model_sets[1:]:
        common_keys &= set(model_dict.keys())
    
    # Build result using data from first provider (they should be similar)
    result = []
    first_set = model_sets[0]
    for key in sorted(common_keys):
        model_data = first_set.get(key)
        if model_data:
            result.append({
                "id": model_data.get("id") or key,
                "model": key,
                "name": model_data.get("name") or key,
                "description": model_data.get("description") or "",
            })
    
    return result


def get_group_with_provider_details(db, group_id: str) -> dict:
    """
    Get a provider group with full details about each member provider.
    
    Args:
        db: Database session
        group_id: ID of the provider group
    
    Returns:
        Group data with enriched member information
    """
    group = get_provider_group(db, group_id)
    members = group.members or []
    
    enriched_members = []
    for member in members:
        provider_id = member.get("provider_id")
        weight = member.get("weight", 1)
        
        try:
            provider = get_llm_provider(db, provider_id)
            enriched_members.append({
                "provider_id": provider_id,
                "weight": weight,
                "name": provider.name,
                "provider": provider.provider,
                "icon": resolve_provider_icon(provider.provider, provider.icon),
                "status": provider.status,
            })
        except HTTPException:
            enriched_members.append({
                "provider_id": provider_id,
                "weight": weight,
                "name": "(Provider not found)",
                "provider": "unknown",
                "icon": None,
                "status": {"available": "unknown"},
            })
    
    return {
        "id": group.id,
        "name": group.name,
        "icon": group.icon,
        "members": enriched_members,
        "created_at": group.created_at.isoformat() if group.created_at else None,
    }


def is_provider_group(db, identifier: str) -> bool:
    """
    Check if an identifier refers to a provider group.
    
    Args:
        db: Database session
        identifier: ID or name to check
    
    Returns:
        True if the identifier is a provider group, False otherwise
    """
    try:
        group = db.query(LLMProviderGroup).filter(LLMProviderGroup.id == identifier).first()
        if group:
            return True
        group = db.query(LLMProviderGroup).filter(LLMProviderGroup.name == identifier).first()
        return group is not None
    except Exception:
        return False


def resolve_provider_for_request(db, provider_id: str, exclude_ids: list[str] | None = None) -> LLMProvider:
    """
    Resolve a provider ID to an actual provider, handling both regular providers and groups.
    
    For regular providers, returns the provider directly.
    For provider groups, performs weighted selection with failover support.
    
    Args:
        db: Database session
        provider_id: ID of provider or provider group
        exclude_ids: Optional list of provider IDs to exclude (for failover retries)
    
    Returns:
        LLMProvider instance to use for the request
    """
    if is_provider_group(db, provider_id):
        return select_provider_from_group(db, provider_id, exclude_ids)
    else:
        return get_llm_provider(db, provider_id)


def build_provider_group_resolution_meta(
    db,
    requested_provider_id: str | None,
    selected_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Build stats metadata that preserves a group request and the concrete recipient."""
    if not requested_provider_id or not is_provider_group(db, requested_provider_id):
        return {}

    try:
        group = get_provider_group(db, requested_provider_id)
    except HTTPException:
        return {}

    selected_provider_name = None
    selected_provider_id = None
    if selected_provider:
        selected_provider_id = getattr(selected_provider, "id", None)
        selected_provider_name = (
            getattr(selected_provider, "name", None)
            or getattr(selected_provider, "provider", None)
            or selected_provider_id
        )

    meta = {
        "requested_provider_id": requested_provider_id,
        "provider_group_id": getattr(group, "id", requested_provider_id),
        "provider_group_name": getattr(group, "name", None),
    }
    if selected_provider_id:
        meta["selected_provider_id"] = selected_provider_id
    if selected_provider_name:
        meta["selected_provider_name"] = selected_provider_name
    return meta
