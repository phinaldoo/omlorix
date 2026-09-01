"""Database queries and persistence for administrator settings."""

from collections.abc import Collection
from typing import Any

from app.llm.models import LLMProvider, Models
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


def get_llm_provider(db: Session, provider_id: str | None) -> LLMProvider | None:
    """Return one configured LLM provider by ID."""

    if not provider_id:
        return None
    return db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()


def list_llm_providers(
    db: Session,
    *,
    provider_types: Collection[str],
    order_by_name: bool = True,
) -> list[LLMProvider]:
    """List providers of the requested types, optionally sorted by display name."""

    query = db.query(LLMProvider).filter(LLMProvider.provider.in_(set(provider_types)))
    if order_by_name:
        query = query.order_by(LLMProvider.name.asc())
    return query.all()


def count_llm_providers(db: Session) -> int:
    """Count configured LLM providers for dashboard metrics."""

    return int(db.query(LLMProvider).count())


def count_active_models(db: Session) -> int:
    """Count active model records for dashboard metrics."""

    return int(db.query(Models).filter(Models.is_active.is_(True)).count())


def list_active_models(
    db: Session,
    *,
    provider_types: Collection[str] | None = None,
) -> list[Models]:
    """List active models, optionally constrained to selected provider types."""

    query = db.query(Models).filter(Models.is_active.is_(True))
    if provider_types is not None:
        query = query.filter(Models.provider.in_(set(provider_types)))
    return query.order_by(Models.name.asc()).all()


def get_active_model(db: Session, model_id: str | None) -> Models | None:
    """Return one active model by ID."""

    if not model_id:
        return None
    return (
        db.query(Models)
        .filter(Models.id == model_id, Models.is_active.is_(True))
        .first()
    )


def persist_settings_json_row(
    db: Session,
    settings_row: Any,
    *,
    mark_modified=flag_modified,
) -> None:
    """Persist in-place changes to one JSON-backed settings row."""

    mark_modified(settings_row, "data")
    db.commit()
    db.refresh(settings_row)
