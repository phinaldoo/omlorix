from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text

from app.database import Base
from app.utils.sqlalchemy_encryption import EncryptedJSON


def _utcnow() -> datetime:
    """Return an aware UTC timestamp for connection lifecycle fields."""
    return datetime.now(timezone.utc)


class SshConnection(Base):
    """Encrypted, user-owned SSH connection reusable by remote capabilities."""

    __tablename__ = "ssh_connections"
    __table_args__ = (Index("ix_ssh_connections_user_id", "user_id"),)

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    icon = Column(Text, nullable=False, default="server")
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=22)
    username = Column(String(128), nullable=False)
    host_key = Column(String(4096), nullable=False)
    config = Column(JSON, nullable=False, default=dict)
    secrets = Column(EncryptedJSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    status = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class UserAcpProfile(Base):
    """A user-owned ACP model launched through a saved SSH connection."""

    __tablename__ = "user_acp_profiles"
    __table_args__ = (
        Index("ix_user_acp_profiles_user_id", "user_id"),
        Index("ix_user_acp_profiles_ssh_connection_id", "ssh_connection_id"),
        Index("ix_user_acp_profiles_model_id", "model_id"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ssh_connection_id = Column(String, ForeignKey("ssh_connections.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(120), nullable=False)
    icon = Column(Text, nullable=False, default="terminal")
    executable = Column(String(1024), nullable=False)
    arguments = Column(JSON, nullable=False, default=list)
    workspace_root = Column(String(4096), nullable=False)
    cwd = Column(String(4096), nullable=False, default=".")
    additional_directories = Column(JSON, nullable=False, default=list)
    mode = Column(String(128), nullable=True)
    permission_mode = Column(String(16), nullable=False, default="ask")
    permission_timeout_seconds = Column(Integer, nullable=False, default=300)
    prompt_timeout_seconds = Column(Integer, nullable=False, default=1800)
    enabled = Column(Boolean, nullable=False, default=True)
    provider_id = Column(String, ForeignKey("llm_provider.id", ondelete="SET NULL"), nullable=True)
    model_id = Column(String, ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
