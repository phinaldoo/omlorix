from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy import JSON, Column, String
from sqlalchemy import Integer as SAInteger
from datetime import datetime, timezone
from sqlalchemy import DateTime
import uuid

from app.database import Base
from app.utils.sqlalchemy_encryption import EncryptedJSON



# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
class Files(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_user_id", "user_id"),
        Index("ix_files_user_last_updated", "user_id", "last_updated_at"),
        Index("ix_files_project_id", "project_id"),
        Index("ix_files_share_id", "share_id"),
        Index("ix_files_created_at", "created_at"),
        Index("ix_files_last_updated_at", "last_updated_at"),
        UniqueConstraint("share_id", name="uq_files_share_id"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String, nullable=False)
    storage_provider = Column(String, nullable=False, default="local")
    storage_key = Column(String, nullable=False, default="")
    storage_meta = Column(JSON, nullable=True)
    file_category = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    file_size = Column(SAInteger, nullable=False)
    project_id = Column(String, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    folder_id = Column(String, nullable=True)
    share = Column(JSON, nullable=True)
    share_id = Column(String, nullable=True)
    meta = Column(JSON, nullable=True)
    # Meta includes internal provenance flags and the original filename.
    created_at = Column(DateTime, nullable=False)
    last_updated_at = Column(DateTime, nullable=False)


class FileProcessingArtifact(Base):
    """Regenerable, encrypted output produced by the File Processing Worker.

    These rows are derived cache data and intentionally excluded from user
    export/import. Deleting the source file cascades the database artifact;
    maintenance removes any corresponding local preview cache file.
    """

    __tablename__ = "file_processing_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "operation",
            "processor_version",
            "cache_key",
            name="uq_file_processing_artifact_cache",
        ),
        Index("ix_file_processing_artifact_file", "file_id", "operation"),
        Index("ix_file_processing_artifact_status", "status", "updated_at"),
        Index("ix_file_processing_artifact_updated", "updated_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id = Column(
        String,
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation = Column(String(32), nullable=False)
    processor_version = Column(SAInteger, nullable=False, default=1)
    cache_key = Column(String(200), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    data = Column(EncryptedJSON, nullable=True)
    cache_path = Column(String, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)


class FileQuotaReservation(Base):
    """Short-lived capacity held for file-producing background/provider work.

    The row is intentionally operational rather than historical. Successful
    persistence consumes it in the same database transaction that creates or
    updates the owned file row; failed work deletes it, and abandoned rows stop
    participating in admission after ``expires_at``.
    """

    __tablename__ = "file_quota_reservations"
    __table_args__ = (
        Index("ix_file_quota_reservations_user_id", "user_id"),
        Index("ix_file_quota_reservations_expires_at", "expires_at"),
        CheckConstraint("reserved_files >= 0", name="ck_file_quota_reservations_files_nonnegative"),
        CheckConstraint("reserved_bytes >= 0", name="ck_file_quota_reservations_bytes_nonnegative"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Reservations are operational rather than user data, so deleting a user
    # must remove any outstanding capacity holds immediately.
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reserved_files = Column(SAInteger, nullable=False, default=0)
    reserved_bytes = Column(BigInteger, nullable=False, default=0)
    purpose = Column(String(100), nullable=False, default="generated_file")
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class FileArtifactShare(Base):
    __tablename__ = "file_artifact_shares"
    __table_args__ = (
        UniqueConstraint("id", name="uq_file_artifact_shares_id"),
        Index("ix_file_artifact_shares_file_id", "file_id"),
        Index("ix_file_artifact_shares_user_id", "user_id"),
        Index("ix_file_artifact_shares_file_created", "file_id", "created_at"),
    )
    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    file_id = Column(String, ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, nullable=False)
    password_hash = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)
    last_accessed_at = Column(DateTime, nullable=True)
    access_count = Column(SAInteger, nullable=False, default=0)


class CanvasAssetGrant(Base):
    """Authoritative permission for one file dependency inside one Canvas.

    Canvas metadata mirrors these rows for portability and user-facing
    provenance, but it is deliberately not an authorization source.  Keeping
    grants in a server-owned table prevents imported or otherwise manipulated
    metadata from manufacturing access to another user's private file.
    """

    __tablename__ = "canvas_asset_grants"
    __table_args__ = (
        UniqueConstraint(
            "canvas_file_id",
            "asset_file_id",
            name="uq_canvas_asset_grants_canvas_asset",
        ),
        Index("ix_canvas_asset_grants_canvas_file_id", "canvas_file_id"),
        Index("ix_canvas_asset_grants_asset_file_id", "asset_file_id"),
        Index("ix_canvas_asset_grants_asset_owner", "asset_owner_user_id"),
        Index("ix_canvas_asset_grants_public_request", "public_request_id"),
    )

    # The membership approval request uses the row ID so notifications never
    # need a second mutable lookup key.
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    canvas_file_id = Column(
        String,
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_file_id = Column(
        String,
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_owner_user_id = Column(String, nullable=False)
    added_by_user_id = Column(String, nullable=False)
    authorized_by_user_id = Column(String, nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    visibility = Column(String(32), nullable=False, default="canvas_members")
    public_status = Column(String(16), nullable=False, default="not_requested")
    public_request_id = Column(String, nullable=True)
    public_authorized_by_user_id = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    authorized_at = Column(DateTime(timezone=True), nullable=True)
    public_authorized_at = Column(DateTime(timezone=True), nullable=True)


class AccessDeniedError(PermissionError):
    pass



# -------------------
# Create file
# -------------------
def create_file(
    db,
    user_id: str,
    file_category: str,
    file_type: str,
    file_size: int,
    project_id: str | None = None,
    share: dict | None = None,
    share_id: str | None = None,
    meta: dict | None = None,
    file_id: str | None = None,
    file_name: str | None = None,
    storage_provider: str | None = None,
    storage_key: str | None = None,
    storage_meta: dict | None = None,
    folder_id: str | None = None,
    commit: bool = True,
):
    """Create a new file record in the database."""
    if not file_name:
        raise ValueError("file_name must be provided when creating a file record")
    resolved_storage_provider = (storage_provider or "local").strip().lower() or "local"
    resolved_storage_key = (storage_key or f"{user_id}/{file_name}").strip()
    if not resolved_storage_key:
        raise ValueError("storage_key must be provided when creating a file record")
        
    file = Files(
        id=file_id if file_id is not None else None,
        user_id=user_id,
        file_name=file_name,
        storage_provider=resolved_storage_provider,
        storage_key=resolved_storage_key,
        storage_meta=storage_meta,
        file_category=file_category,
        file_type=file_type,
        file_size=file_size,
        project_id=project_id,
        folder_id=folder_id,
        share=share,
        share_id=share_id,
        meta=meta,
        created_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc),
        )
    db.add(file)
    if commit:
        db.commit()
        db.refresh(file)
    else:
        db.flush()
    return file



# -------------------
# Get file
# -------------------
def get_file(db, file_id: str, user_id: str):
    """Get a file by ID and user ID."""
    return db.query(Files).filter(Files.id == file_id, Files.user_id == user_id).first()


def query_files(db, user_id: str):
    """Build the base query for files owned by a user."""
    return db.query(Files).filter(Files.user_id == user_id)



# -------------------
# List files
# -------------------
def list_files(db, user_id: str, *, limit: int | None = None, offset: int = 0):
    """List files for a user, optionally bounded by limit/offset."""
    query = query_files(db, user_id).order_by(Files.created_at.asc(), Files.id.asc())
    if offset > 0:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def count_files(db, user_id: str) -> int:
    """Count files owned by a user."""
    return query_files(db, user_id).count()


def iter_files(db, user_id: str, *, batch_size: int = 500):
    """Iterate files for a user without materializing the full result set."""
    query = query_files(db, user_id).order_by(Files.created_at.asc(), Files.id.asc())
    if hasattr(query, "execution_options"):
        query = query.execution_options(stream_results=True)
    if hasattr(query, "yield_per"):
        query = query.yield_per(max(1, int(batch_size or 1)))
    yield from query



# -------------------
# List project files
# -------------------
def list_project_files(db, user_id: str, project_id: str):
    """List all files for a project that the user has access to."""
    from app.projects.models import has_project_access

    normalized_user_id = str(user_id or "").strip()
    normalized_project_id = str(project_id or "").strip()

    if not normalized_user_id:
        raise ValueError("user_id is required")
    if not normalized_project_id:
        raise ValueError("project_id is required")

    if not has_project_access(db, normalized_user_id, normalized_project_id):
        raise AccessDeniedError("Access denied to project files")
    return db.query(Files).filter(Files.project_id == normalized_project_id).all()
