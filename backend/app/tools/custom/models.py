from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Session

from app.database import Base


class CustomPythonTool(Base):
    """Persist a custom Python tool definition that admins can manage and expose at runtime."""

    __tablename__ = "custom_python_tool"
    __table_args__ = (
        Index("ix_custom_python_tool_enabled", "enabled"),
        UniqueConstraint("name", name="uq_custom_python_tool_name"),
    )

    id = Column(String, primary_key=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    source_code = Column(Text, nullable=False)
    tool_schema = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    timeout_seconds = Column(Integer, nullable=False, default=30, server_default="30")
    created_at = Column(DateTime, nullable=False, default=func.now(), server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), server_default=func.now())


MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300


def _normalize_timeout_seconds(timeout_seconds: int | None) -> int:
    """Validate and normalize persisted timeout values for custom Python tools."""

    try:
        timeout_value = int(timeout_seconds or 30)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer.") from exc
    if timeout_value < MIN_TIMEOUT_SECONDS or timeout_value > MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS} seconds."
        )
    return timeout_value


def list_custom_python_tools(db: Session, *, enabled_only: bool = False) -> list[CustomPythonTool]:
    """List stored custom Python tools, optionally restricted to enabled records only."""

    query = db.query(CustomPythonTool)
    if enabled_only:
        query = query.filter(CustomPythonTool.enabled.is_(True))
    return query.order_by(CustomPythonTool.display_name.asc(), CustomPythonTool.name.asc()).all()


def get_custom_python_tool(db: Session, tool_id: str) -> CustomPythonTool:
    """Load a custom Python tool by identifier or raise the appropriate API error."""

    identifier = str(tool_id or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Invalid custom tool id.")
    tool = db.query(CustomPythonTool).filter(CustomPythonTool.id == identifier).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Custom Python tool not found.")
    return tool


def get_custom_python_tool_by_name(
    db: Session,
    name: str,
    *,
    enabled_only: bool = False,
) -> CustomPythonTool | None:
    """Resolve a custom Python tool by name for uniqueness checks and runtime execution."""

    normalized_name = str(name or "").strip()
    if not normalized_name:
        return None
    query = db.query(CustomPythonTool).filter(CustomPythonTool.name == normalized_name)
    if enabled_only:
        query = query.filter(CustomPythonTool.enabled.is_(True))
    return query.first()


def create_custom_python_tool(
    db: Session,
    *,
    name: str,
    display_name: str,
    description: str,
    source_code: str,
    tool_schema: dict[str, Any],
    enabled: bool = True,
    timeout_seconds: int = 30,
) -> CustomPythonTool:
    """Create and persist a validated custom Python tool record."""

    timeout_value = _normalize_timeout_seconds(timeout_seconds)
    tool = CustomPythonTool(
        name=name,
        display_name=display_name,
        description=description,
        source_code=source_code,
        tool_schema=tool_schema,
        enabled=bool(enabled),
        timeout_seconds=timeout_value,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool


def update_custom_python_tool(
    db: Session,
    tool_id: str,
    *,
    name: str,
    display_name: str,
    description: str,
    source_code: str,
    tool_schema: dict[str, Any],
    enabled: bool,
    timeout_seconds: int,
) -> CustomPythonTool:
    """Replace the stored definition and metadata for an existing custom Python tool."""

    timeout_value = _normalize_timeout_seconds(timeout_seconds)
    tool = get_custom_python_tool(db, tool_id)
    tool.name = name
    tool.display_name = display_name
    tool.description = description
    tool.source_code = source_code
    tool.tool_schema = tool_schema
    tool.enabled = bool(enabled)
    tool.timeout_seconds = timeout_value
    tool.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(tool)
    return tool


def delete_custom_python_tool(db: Session, tool_id: str) -> None:
    """Delete a custom Python tool after confirming that the identifier exists."""

    tool = get_custom_python_tool(db, tool_id)
    db.delete(tool)
    db.commit()
