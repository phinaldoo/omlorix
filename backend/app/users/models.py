from copy import deepcopy
from collections.abc import Callable
from datetime import datetime, timezone, timedelta
import logging
from sqlalchemy import Column, String, Boolean, DateTime, exists, func, text
from fastapi import HTTPException
from sqlalchemy import Index
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import validates
import uuid
from typing import Any

from app.database import Base
from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.roles import (
    ASSIGNABLE_ROLES,
    OWNER_ROLE,
    is_admin_role,
    is_owner_role,
)
from app.utils.email import normalize_email
from app.utils.sqlalchemy_encryption import EncryptedJSON, EncryptedString


ACCOUNT_TYPE_REGULAR = "regular"
ACCOUNT_TYPE_TEMPORARY = "temporary"
AUTH_MANAGEMENT_LOCAL = "local"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    # Identifiers & Relationships
    id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    email = Column(String, index=True, nullable=False)
    group_id = Column(String, nullable=False)
    account_type = Column(String, nullable=False, default=ACCOUNT_TYPE_REGULAR)
    temporary_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    provisioned_by_user_id = Column(String, nullable=True)

    # Authentication & Security
    hashed_password = Column(String, nullable=False)
    # This is an authoritative policy field rather than a derived SSO-link
    # hint.  Deriving it from provider settings would allow a user to regain
    # local authentication by unlinking or partially corrupting that provider.
    auth_management_mode = Column(String, nullable=False, default=AUTH_MANAGEMENT_LOCAL)
    external_auth_provider = Column(String, nullable=True)
    externally_managed_at = Column(DateTime(timezone=True), nullable=True)
    lock = Column(
        EncryptedJSON,
        default=lambda: {
            "is_locked": False,
            "lock_until": None,
            "type": "",
            "reason": "",
        },
    )  # Lock for wrong signin attempts...

    # Personal Information
    first_name = Column(EncryptedString, nullable=False)
    last_name = Column(EncryptedString, nullable=False)

    # User Settings / Preferences
    role = Column(String, default="pending", nullable=False)
    settings = Column(
        MutableDict.as_mutable(EncryptedJSON),
        nullable=False,
        default=lambda: deepcopy(DEFAULT_USER_SETTINGS),
    )  # JSON representation of settings
    last_model = Column(String, nullable=True)  # Last used model
    custom_profile_picture = Column(Boolean, nullable=False, default=False)

    # Status Flags
    is_active = Column(Boolean, default=True)

    # Soft-delete / Retention fields
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    deletion_scheduled_for = Column(DateTime(timezone=True), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False)
    last_active_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_users_group_id", "group_id"),
        Index("ix_users_group_id_account_type", "group_id", "account_type"),
        Index(
            "ix_users_temporary_account_retention",
            "account_type",
            "deleted_at",
            "temporary_expires_at",
        ),
        Index("ix_users_role", "role"),
        Index("ix_users_is_active", "is_active"),
        Index("ix_users_auth_management_mode", "auth_management_mode"),
        Index("ix_users_created_at", "created_at"),
        Index("ix_users_last_active_at", "last_active_at"),
        Index(
            "ux_users_single_owner",
            "role",
            unique=True,
            postgresql_where=text("role = 'owner'"),
            sqlite_where=text("role = 'owner'"),
        ),
        Index("ux_users_email_canonical", func.lower(func.trim(email)), unique=True),
    )

    @validates("email")
    def _normalize_email_value(self, _key, value):
        normalized = normalize_email(value)
        return normalized if normalized is not None else value


def canonicalize_user_email(value: str | None) -> str | None:
    return normalize_email(value)


def build_user_email_match(email: str | None):
    normalized = canonicalize_user_email(email)
    if not normalized:
        return None
    return func.lower(func.trim(User.email)) == normalized


def normalize_utc_datetime(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# -------------------
# Create user
# -------------------
def create_user(
    db,
    email,
    hashed_password,
    first_name,
    last_name,
    role,
    group_id,
    user_id=None,
    account_type="regular",
    temporary_expires_at=None,
    provisioned_by_user_id=None,
    *,
    commit: bool = True,
    refresh: bool = True,
):
    normalized_email = canonicalize_user_email(email)
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")
    count = db.query(User).count()
    if count == 0:
        # The first account owns the instance. The owner role cannot be
        # assigned later through ordinary APIs or external identity systems.
        role = OWNER_ROLE
    user_kwargs = {}
    if user_id:
        user_kwargs["id"] = user_id

    user = User(
        email=normalized_email,
        hashed_password=hashed_password,
        first_name=first_name,
        last_name=last_name,
        role=role,
        group_id=group_id,
        account_type=account_type or "regular",
        temporary_expires_at=normalize_utc_datetime(temporary_expires_at),
        provisioned_by_user_id=provisioned_by_user_id,
        settings=deepcopy(DEFAULT_USER_SETTINGS),
        created_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
        **user_kwargs,
    )

    default_model_id = _get_default_model_id(db)
    if default_model_id:
        user.last_model = default_model_id
        chat_settings = (
            user.settings.get("chat") if isinstance(user.settings, dict) else None
        )
        if isinstance(chat_settings, dict):
            chat_settings["last_model"] = default_model_id

    db.add(user)
    if commit:
        db.commit()
    else:
        db.flush()
    if refresh:
        db.refresh(user)
    return user


# -------------------
# Get active user count
# -------------------
def get_active_user_count(db):
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    return (
        db.query(User)
        .filter(User.is_active.is_(True), User.last_active_at >= one_week_ago)
        .count()
    )


# -------------------
# Get pending user count
# -------------------
def get_pending_user_count(db):
    """Count pending users that remain visible in normal administration views."""
    return (
        db.query(User)
        .filter(
            User.role == "pending",
            User.deleted_at.is_(None),
        )
        .count()
    )


# -------------------
# List users
# -------------------
def list_all_users(db, include_deleted: bool = True):
    query = db.query(User).order_by(User.last_active_at.desc())
    if not include_deleted:
        query = query.filter(User.deleted_at.is_(None))
    return query.all()


def query_admin_users_page(
    db,
    *,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[User], int]:
    """Query one stable admin user page and its filtered total.

    Names are encrypted at rest and therefore cannot be safely searched with
    SQL pattern matching. Administrative search is consequently performed on
    the indexed plaintext email field instead of loading and decrypting every
    user row in Python.
    """
    safe_offset = max(int(offset or 0), 0)
    safe_limit = None if limit is None else max(int(limit), 0)

    query = db.query(User).filter(User.deleted_at.is_(None))
    normalized_search = str(search or "").strip().lower()
    if normalized_search:
        query = query.filter(
            func.lower(User.email).contains(normalized_search, autoescape=True)
        )

    total = query.order_by(None).count()
    query = query.order_by(User.last_active_at.desc(), User.id.asc())
    if safe_offset:
        query = query.offset(safe_offset)
    if safe_limit is not None:
        query = query.limit(safe_limit)
    return query.all(), total


# -------------------
# User exists by email
# -------------------
def user_exists_by_email(db, email: str) -> bool:
    email_match = build_user_email_match(email)
    if email_match is None:
        return False
    return bool(db.query(exists().where(email_match)).scalar())


# -------------------
# Get user
# -------------------
def get_user(db, user_id=None, email=None):
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
    elif email:
        email_match = build_user_email_match(email)
        if email_match is None:
            raise HTTPException(status_code=404, detail="User not found")
        user = db.query(User).filter(email_match).first()
    else:
        raise HTTPException(status_code=404, detail="Error while getting user")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# -------------------
# Update user first name
# -------------------
def update_user_first_name(db, user_id, first_name):
    user = db.query(User).filter(User.id == user_id).first()
    user.first_name = first_name
    db.commit()
    db.refresh(user)
    return user


# -------------------
# Update user last name
# -------------------
def update_user_last_name(db, user_id, last_name):
    user = db.query(User).filter(User.id == user_id).first()
    user.last_name = last_name
    db.commit()
    db.refresh(user)
    return user


# -------------------
# Check if user email already exists
# -------------------
def user_email_exists(db, user_id, email):
    normalized_email = canonicalize_user_email(email)
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if canonicalize_user_email(user.email) == normalized_email:
        raise HTTPException(status_code=409, detail="Email already in use")
    return True


# -------------------
# Update user email
# -------------------
def update_user_email(db, user_id, email):
    normalized_email = canonicalize_user_email(email)
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")
    user = db.query(User).filter(User.id == user_id).first()
    user.email = normalized_email
    db.commit()
    db.refresh(user)
    return user


# -------------------
# Change User Role
# -------------------
def change_user_role(user_id, role, db):
    """Assign an owner-approved, non-owner account role."""

    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role.")
    user = get_user(db, user_id)
    if role == "pending" and user.role != "pending":
        from app.groups.models import ensure_user_can_become_ineligible_manager

        ensure_user_can_become_ineligible_manager(db, user.id)
    user.role = role
    db.commit()
    db.refresh(user)
    return True


# -------------------
# Change User Last Model
# -------------------
def change_user_last_model(user_id, last_model, db):
    user = get_user(db, user_id)
    user.last_model = last_model
    db.commit()
    db.refresh(user)
    return True


def _get_default_model_id(db):
    try:
        from app.settings.utils import (
            get_value_by_page_and_key,
        )  # Imported lazily to avoid circular dependencies
    except Exception:
        return None

    try:
        raw_value = get_value_by_page_and_key("models", "default_model", db)
    except Exception:
        return None

    if isinstance(raw_value, str):
        raw_value = raw_value.strip()
        return raw_value or None

    return None


# -------------------
# Lock user
# -------------------
def lock_user(db, user_id: str, lock_until, type, reason):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if isinstance(lock_until, datetime):
        lock_until_value = lock_until.isoformat()
    else:
        lock_until_value = lock_until
    user.lock = {
        "is_locked": True,
        "lock_until": lock_until_value,
        "type": type,
        "reason": reason,
    }
    db.commit()
    db.refresh(user)
    return user


def _default_user_lock():
    return {"is_locked": False, "lock_until": None, "type": "", "reason": ""}


def _parse_user_lock_until(lock_until_raw):
    if isinstance(lock_until_raw, str) and lock_until_raw:
        try:
            return normalize_utc_datetime(datetime.fromisoformat(lock_until_raw))
        except ValueError:
            return None
    if isinstance(lock_until_raw, datetime):
        return normalize_utc_datetime(lock_until_raw)
    return None


def evaluate_user_lock(user, db=None):
    lock_value = getattr(user, "lock", None)
    lock = lock_value if isinstance(lock_value, dict) else {}
    is_locked = bool(lock.get("is_locked"))
    lock_until = _parse_user_lock_until(lock.get("lock_until"))

    if is_locked:
        if lock_until and lock_until <= datetime.now(timezone.utc):
            user.lock = _default_user_lock()
            if db is not None:
                db.commit()
                db.refresh(user)
            return False
        return {
            "is_locked": True,
            "lock_until": lock_until,
            "type": lock.get("type"),
            "reason": lock.get("reason"),
        }
    return False


# -------------------
# Check user locked
# -------------------
def check_user_locked(db, user_id):
    user = db.query(User).filter(User.id == user_id).first()
    # If the user does not exist, we treat them as *not* locked instead of
    # raising an exception. This avoids returning an internal server error
    # when somebody tries to sign in with an email address that is not in
    # the database.
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return evaluate_user_lock(user, db)


def get_user_wrong_sign_in_attempts(db, user_id):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    settings = user.settings or {}
    if not isinstance(settings, dict):
        return 0
    secret_settings = settings.get("secret")
    if not isinstance(secret_settings, dict):
        return 0
    value = secret_settings.get("wrong_sign_in_attempts", 0)
    if not isinstance(value, int):
        return 0
    return value


def increment_user_wrong_sign_in_attempts(db, user_id):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    settings = user.settings or {}
    if not isinstance(settings, dict):
        settings = {}
    secret_settings = settings.get("secret")
    if not isinstance(secret_settings, dict):
        secret_settings = {}
        settings["secret"] = secret_settings

    raw_value = secret_settings.get("wrong_sign_in_attempts", 0)
    try:
        attempts = int(raw_value)
    except (TypeError, ValueError):
        attempts = 0

    attempts += 1
    secret_settings["wrong_sign_in_attempts"] = attempts

    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    db.refresh(user)
    return attempts


def reset_user_wrong_sign_in_attempts(db, user_id):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    settings = user.settings or {}
    if not isinstance(settings, dict):
        settings = {}
    secret_settings = settings.get("secret")
    if not isinstance(secret_settings, dict):
        secret_settings = {}
        settings["secret"] = secret_settings
    secret_settings["wrong_sign_in_attempts"] = 0
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    db.refresh(user)
    return True


# -------------------
# Update user profile picture boolean
# -------------------
def update_user_profile_picture_boolean(db, user_id, boolean):
    user = get_user(db, user_id, None)
    user.custom_profile_picture = boolean
    db.commit()
    db.refresh(user)
    return True


# -------------------
# Update last active user
# -------------------
def update_last_active_user(db, user_id):
    user = get_user(db, user_id, None)
    user.last_active_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return True


# -------------------
# Set user activation status
# -------------------
def set_user_activation_status(db, user_id, is_active: bool):
    user = get_user(db, user_id, None)
    if not is_active and user.is_active:
        from app.groups.models import ensure_user_can_become_ineligible_manager

        ensure_user_can_become_ineligible_manager(db, user.id)
    user.is_active = is_active
    if not is_active:
        from app.workers.models import cancel_user_worker_jobs

        cancel_user_worker_jobs(db, user_id=user.id, commit=False)
    db.commit()
    db.refresh(user)
    return True


# -------------------
# Delete user
# -------------------
def soft_delete_user(
    db,
    user_id: str,
    scheduled_for: datetime | None = None,
    *,
    allow_administrative_target: bool = False,
    commit: bool = True,
) -> User:
    """Mark an account as soft-deleted.

    Administrative accounts remain protected by default. The admin router may
    opt in only after it has authenticated the instance owner and applied the
    owner/admin hierarchy checks. Keeping the default fail-closed protects
    self-service deletion and any future internal callers.
    """
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if is_owner_role(user.role):
        # The owner role is never transferable or deletable. This invariant is
        # enforced again here so a mistaken internal authorization flag cannot
        # remove the only account capable of managing other administrators.
        raise HTTPException(status_code=409, detail="Cannot delete the owner account.")
    if is_admin_role(user.role) and not allow_administrative_target:
        raise HTTPException(
            status_code=409, detail="Cannot delete an administrator account."
        )
    if user.deleted_at is not None:
        raise HTTPException(status_code=409, detail="User is already deleted.")

    from app.groups.models import ensure_user_can_become_ineligible_manager

    ensure_user_can_become_ineligible_manager(db, user.id)

    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.deletion_scheduled_for = scheduled_for
    user.is_active = False
    from app.workers.models import cancel_user_worker_jobs

    cancel_user_worker_jobs(db, user_id=user.id, commit=False)
    if scheduled_for is not None:
        # Stage the purge in the same transaction as the soft-deletion marker.
        # Cancellation/restoration changes the marker, which the worker
        # revalidates before any destructive action.
        from app.workers.lifecycle import enqueue_scheduled_hard_delete

        enqueue_scheduled_hard_delete(
            db,
            user_id=user.id,
            scheduled_for=scheduled_for,
            commit=False,
        )
    if commit:
        db.commit()
        db.refresh(user)
    else:
        db.flush()
    return user


def restore_user_state(
    db,
    user_id: str,
    *,
    allow_already_active: bool = False,
    commit: bool = False,
) -> User:
    """Restore account state under the cross-database audit-erasure guard.

    Callers that need to update additional account fields (for example SCIM)
    can keep this operation in their existing transaction.  The guard must be
    acquired before the user row so restoration cannot become visible while a
    policy-aware audit deletion is between its main- and audit-schema commits.
    """

    # This row is separate from the subject fence so an audit erasure can
    # commit its main-DB fence while retaining one lock until audit-DB cleanup
    # finishes. Restoration takes it before the User row and cannot become
    # visible in the middle of that cross-database operation.
    from app.workers.models import lock_audit_event_erasure_guard

    lock_audit_event_erasure_guard(db, user_id=user_id)
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.deleted_at is None and not allow_already_active:
        raise HTTPException(status_code=409, detail="User is not deleted.")

    from app.email.models import cancel_user_security_events

    cancel_user_security_events(
        db,
        user_id,
        event_types=("account_deactivated", "account_deletion_scheduled"),
        commit=False,
    )
    user.deleted_at = None
    user.deletion_scheduled_for = None
    user.is_active = True
    from app.workers.models import restore_user_audit_event_subject

    restore_user_audit_event_subject(db, user_id=user.id, commit=False)
    if commit:
        db.commit()
        db.refresh(user)
    else:
        db.flush()
    return user


def restore_user(db, user_id: str) -> User:
    """Restore a soft-deleted user, allowing them to log in again."""

    return restore_user_state(db, user_id, commit=True)


def cancel_scheduled_deletion(db, user_id: str) -> User:
    """
    Cancel the scheduled permanent deletion but keep the user soft-deleted.
    User remains deleted but won't be auto-purged.
    """
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.deleted_at is None:
        raise HTTPException(status_code=409, detail="User is not deleted.")
    if user.deletion_scheduled_for is None:
        raise HTTPException(status_code=409, detail="No scheduled deletion to cancel.")

    from app.email.models import cancel_user_security_events

    cancel_user_security_events(
        db,
        user_id,
        event_types=("account_deletion_scheduled",),
        commit=False,
    )
    user.deletion_scheduled_for = None
    db.commit()
    db.refresh(user)
    return user


def list_pending_deletion_users(db) -> list[User]:
    """
    List ordinary users that are soft-deleted and pending permanent deletion.

    Temporary accounts remain visible in their group's management screen with
    their expiry and purge deadline. Excluding them here avoids presenting the
    generic "restore user" action, which cannot make an expired credential
    usable again.
    """
    return (
        db.query(User)
        .filter(User.deleted_at.isnot(None))
        .filter(User.account_type != ACCOUNT_TYPE_TEMPORARY)
        .order_by(User.deletion_scheduled_for.asc().nullsfirst())
        .all()
    )


def hard_delete_user(
    db,
    user_id: str,
    *,
    allow_administrative_target: bool = False,
    record_erasure: bool = True,
    notify_user: bool = True,
) -> bool:
    """Permanently delete an account and all of its data.

    Administrative accounts require an explicit authorization signal from an
    owner-authorized request or from the scheduled-deletion worker processing
    an already approved soft deletion.
    """
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if not user:
        return False
    if is_owner_role(user.role):
        raise HTTPException(status_code=409, detail="Cannot delete the owner account.")
    if is_admin_role(user.role) and not allow_administrative_target:
        raise HTTPException(
            status_code=409, detail="Cannot delete an administrator account."
        )

    erasure_record: dict[str, Any] | None = None
    if record_erasure:
        # Persist an intent before mutating the database. A crash after commit
        # but before the completion append can then be resolved from the live
        # database at startup and cannot silently resurrect through restore.
        from app.users.deletion_policy import (
            get_audit_log_user_deletion_retention_policy,
            get_auth_log_user_deletion_retention_policy,
        )
        erasure_recorded_at = datetime.now(timezone.utc)
        erasure_record = {
            "erased_at": erasure_recorded_at,
            "retention_started_at": getattr(user, "deleted_at", None)
            or erasure_recorded_at,
            "auth_policy": get_auth_log_user_deletion_retention_policy(db),
            "audit_policy": get_audit_log_user_deletion_retention_policy(db),
        }

    from app.groups.models import (
        GroupManager,
        ensure_user_can_become_ineligible_manager,
    )

    ensure_user_can_become_ineligible_manager(db, user.id)

    from app.auth.models import (
        Authentication,
        PasskeyCredential,
        PendingAuthAction,
        PasswordResetToken,
        SocialAuthIdentity,
        WebAuthnChallenge,
    )
    from app.auth.session_store import revoke_user_sessions
    from app.agents.models import delete_user_linked_agents
    from app.chats.models import (
        ChatMessages,
        Chats,
        _cleanup_deep_research_artifacts_after_commit,
        _delete_deep_research_runs_for_user,
    )
    from app.connections.models import ConnectionOAuthState, UserConnection
    from app.feedback.models import ModelFeedback
    from app.file_folders.models import FileFolders, SharedFileFolderSubscription
    from app.files.models import FileArtifactShare, Files
    from app.files.utils import delete_storage_reference
    from app.files.storage import (
        get_local_user_files_base_dir,
        get_user_file_storage_adapter_for_provider,
    )
    from app.llm.models import ModelSettingPresets
    from app.llmstats.models import LLMGenerationStatistic, ToolCallStatistic
    from app.mcp.models import MCPOAuthState, MCPServer
    from app.memories.models import Memory
    from app.notes.models import NoteHistory, Notes, SharedNoteSubscription
    from app.projects.models import (
        Project,
        ProjectMember,
        _delete_projects_and_related_data,
    )
    from app.prompts.models import Prompts, SharedPromptSubscription
    from app.realtime.models import RealtimeSession
    from app.scim.models import ScimGroupMembership, ScimUserLink
    from app.skills.models import (
        Skills,
        SharedSkillSubscription,
        _delete_skill_directory,
    )
    from app.automations.models import Automation
    from app.todos.models import SharedTodoListSubscription, TodoLists, Todos
    from app.tools.slide_presentation.models import SlidePresentations
    from app.tools.slide_presentation.storage import delete_slide_presentation_artifacts
    from app.userNotifications.models import remove_user_references_from_notifications
    import shutil

    revoke_user_sessions(user_id)
    post_commit_cleanup_actions: list[Callable[[], None]] = []
    if erasure_record is not None:
        # All eligibility checks and read-only cleanup planning have completed.
        # Publish the durable intent immediately before the destructive
        # transaction so a rejected precondition cannot leave a privacy-biased
        # intent that a later backup restore would mistake for a committed
        # erasure.
        from app.users.erasure_ledger import record_user_erasure_intent

        erasure_record["operation_id"] = record_user_erasure_intent(
            user_id,
            auth_policy=erasure_record["auth_policy"],
            audit_policy=erasure_record["audit_policy"],
            erased_at=erasure_record["erased_at"],
            retention_started_at=erasure_record["retention_started_at"],
        )

    commit_started = False
    try:
        if erasure_record is not None:
            audit_policy = erasure_record["audit_policy"]
            if audit_policy.get("mode") == "delete_instantly" or bool(
                audit_policy.get("delete_immediately")
            ):
                # Commit the privacy fence, event-job cancellation, and outbox
                # redaction atomically with permanent account deletion. If the
                # process dies before the cross-database audit-row cleanup, no
                # queued event can recreate the erased subject in the meantime.
                from app.workers.models import erase_user_audit_event_state

                erase_user_audit_event_state(
                    db,
                    user_id=user_id,
                    commit=False,
                )
                from app.workers.events import enqueue_audit_erasure

                enqueue_audit_erasure(
                    db,
                    user_id=user_id,
                    boundary_id=erasure_record["operation_id"],
                    commit=False,
                )

        from app.workers.models import erase_user_worker_state

        erase_user_worker_state(db, user_id=user_id, commit=False)
        from app.email.models import (
            PendingEmailChange,
            TrustedDeviceNotification,
            erase_user_email_state,
        )
        from app.email.service import enqueue_security_event

        # Permanent erasure removes active and terminal outbox rows, including
        # idempotency keys that may contain an internal user identifier. The
        # final notice below is detached from the deleted account.
        erase_user_email_state(db, user_id, commit=False)
        if notify_user:
            enqueue_security_event(
                db,
                user=user,
                event_type="account_deleted",
                source_id=f"hard-delete:{datetime.now(timezone.utc).isoformat()}",
                priority=0,
                detach_user_id=True,
            )
        db.query(PendingEmailChange).filter(
            PendingEmailChange.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(TrustedDeviceNotification).filter(
            TrustedDeviceNotification.user_id == user_id
        ).delete(synchronize_session=False)

        # Remove active authentications and related security records first.
        (
            db.query(Authentication)
            .filter(Authentication.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(PasskeyCredential)
            .filter(PasskeyCredential.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(SocialAuthIdentity)
            .filter(SocialAuthIdentity.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(WebAuthnChallenge)
            .filter(WebAuthnChallenge.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(PendingAuthAction)
            .filter(PendingAuthAction.user_id == user_id)
            .delete(synchronize_session=False)
        )

        # Delete user-owned skills and both inbound/outbound subscriptions.
        user_skill_ids = [
            skill_id
            for (skill_id,) in db.query(Skills.id)
            .filter(Skills.user_id == user_id)
            .all()
        ]
        if user_skill_ids:
            (
                db.query(SharedSkillSubscription)
                .filter(SharedSkillSubscription.skill_id.in_(user_skill_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(Skills)
                .filter(Skills.id.in_(user_skill_ids))
                .delete(synchronize_session=False)
            )
            for skill_id in user_skill_ids:
                post_commit_cleanup_actions.append(
                    lambda skill_id=skill_id: _delete_skill_directory(user_id, skill_id)
                )

        (
            db.query(SharedSkillSubscription)
            .filter(SharedSkillSubscription.subscriber_id == user_id)
            .delete(synchronize_session=False)
        )

        # Delete todo lists/todos owned by the user and all related subscriptions.
        user_todo_list_rows = (
            db.query(TodoLists.id).filter(TodoLists.user_id == user_id).all()
        )
        if user_todo_list_rows:
            todo_list_ids = [todo_list_id for (todo_list_id,) in user_todo_list_rows]
            (
                db.query(SharedTodoListSubscription)
                .filter(SharedTodoListSubscription.todo_list_id.in_(todo_list_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(Todos)
                .filter(Todos.todo_list.in_(todo_list_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(TodoLists)
                .filter(TodoLists.id.in_(todo_list_ids))
                .delete(synchronize_session=False)
            )
        (
            db.query(SharedTodoListSubscription)
            .filter(SharedTodoListSubscription.subscriber_id == user_id)
            .delete(synchronize_session=False)
        )

        # Delete notes, note history, and note subscriptions.
        user_note_ids = [
            note_id
            for (note_id,) in db.query(Notes.id).filter(Notes.user_id == user_id).all()
        ]
        if user_note_ids:
            (
                db.query(SharedNoteSubscription)
                .filter(SharedNoteSubscription.note_id.in_(user_note_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(NoteHistory)
                .filter(NoteHistory.note_id.in_(user_note_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(Notes)
                .filter(Notes.id.in_(user_note_ids))
                .delete(synchronize_session=False)
            )
        (
            db.query(NoteHistory)
            .filter(NoteHistory.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(SharedNoteSubscription)
            .filter(SharedNoteSubscription.subscriber_id == user_id)
            .delete(synchronize_session=False)
        )
        # Capture every Deep Research workspace before deleting the user. Runs
        # without a persisted chat would otherwise be removed by the user FK
        # cascade without leaving enough metadata to clean cloud artifacts.
        deep_research_cleanup_descriptors = _delete_deep_research_runs_for_user(
            db,
            user_id=user_id,
        )
        if deep_research_cleanup_descriptors:
            post_commit_cleanup_actions.append(
                lambda descriptors=deep_research_cleanup_descriptors: (
                    _cleanup_deep_research_artifacts_after_commit(descriptors)
                )
            )

        # Delete chats/messages owned by the user.
        user_chat_ids = [
            chat_id
            for (chat_id,) in db.query(Chats.id).filter(Chats.user_id == user_id).all()
        ]
        if user_chat_ids:
            (
                db.query(ChatMessages)
                .filter(ChatMessages.chat_id.in_(user_chat_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(Chats)
                .filter(Chats.id.in_(user_chat_ids))
                .delete(synchronize_session=False)
            )

        # Delete files and share rows now; defer physical storage deletion until after commit.
        user_files = db.query(Files).filter(Files.user_id == user_id).all()
        user_file_ids = [file_row.id for file_row in user_files]
        user_files_base_dir = get_local_user_files_base_dir()
        for file_row in user_files:
            storage_provider = (
                str(getattr(file_row, "storage_provider", "") or "").strip().lower()
                or "local"
            )
            storage_key = str(getattr(file_row, "storage_key", "") or "").strip()
            if not storage_key:
                storage_key = f"{user_id}/{file_row.file_name}"
            if storage_provider == "local":
                post_commit_cleanup_actions.append(
                    lambda storage_key=storage_key, file_name=file_row.file_name: (
                        delete_storage_reference(
                            storage_provider="local",
                            storage_key=storage_key,
                            user_id=user_id,
                            file_name=file_name,
                        )
                    )
                )
            elif storage_key:
                adapter = get_user_file_storage_adapter_for_provider(storage_provider)
                post_commit_cleanup_actions.append(
                    lambda adapter=adapter, storage_key=storage_key: (
                        adapter.delete_file(storage_key)
                    )
                )

        if user_file_ids:
            (
                db.query(FileArtifactShare)
                .filter(FileArtifactShare.file_id.in_(user_file_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(Files)
                .filter(Files.id.in_(user_file_ids))
                .delete(synchronize_session=False)
            )

        (
            db.query(FileArtifactShare)
            .filter(FileArtifactShare.user_id == user_id)
            .delete(synchronize_session=False)
        )

        # Delete folders and folder-sharing rows linked to the user.
        user_folder_rows = (
            db.query(FileFolders.id).filter(FileFolders.user_id == user_id).all()
        )
        if user_folder_rows:
            user_folder_ids = [folder_id for (folder_id,) in user_folder_rows]
            (
                db.query(SharedFileFolderSubscription)
                .filter(SharedFileFolderSubscription.folder_id.in_(user_folder_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(FileFolders)
                .filter(FileFolders.id.in_(user_folder_ids))
                .delete(synchronize_session=False)
            )
        (
            db.query(SharedFileFolderSubscription)
            .filter(SharedFileFolderSubscription.subscriber_id == user_id)
            .delete(synchronize_session=False)
        )

        # Remaining user-linked feature data.
        # Group-manager rows are user-owned delegation records. Delete them
        # explicitly for SQLite/tests and defense in depth alongside the
        # database-level ON DELETE CASCADE used by PostgreSQL.
        db.query(GroupManager).filter(GroupManager.user_id == user_id).delete(
            synchronize_session=False
        )
        delete_user_linked_agents(
            db, user_id, cleanup_actions=post_commit_cleanup_actions
        )

        for presentation in (
            db.query(SlidePresentations)
            .filter(SlidePresentations.user_id == user_id)
            .all()
        ):
            post_commit_cleanup_actions.append(
                lambda storage_provider=presentation.storage_provider, storage_prefix=presentation.storage_prefix, slide_count=presentation.slide_count: (
                    delete_slide_presentation_artifacts(
                        storage_provider=storage_provider,
                        storage_prefix=storage_prefix,
                        slide_count=slide_count,
                    )
                )
            )
            db.delete(presentation)

        db.query(ScimUserLink).filter(ScimUserLink.user_id == user_id).delete(
            synchronize_session=False
        )
        (
            db.query(ScimGroupMembership)
            .filter(ScimGroupMembership.user_id == user_id)
            .delete(synchronize_session=False)
        )
        remove_user_references_from_notifications(db, user_id)

        db.query(Memory).filter(Memory.user_id == user_id).delete(
            synchronize_session=False
        )
        db.query(Automation).filter(Automation.user_id == user_id).delete(
            synchronize_session=False
        )
        db.query(UserConnection).filter(UserConnection.user_id == user_id).delete(
            synchronize_session=False
        )
        (
            db.query(ConnectionOAuthState)
            .filter(ConnectionOAuthState.user_id == user_id)
            .delete(synchronize_session=False)
        )
        db.query(ModelFeedback).filter(ModelFeedback.user_id == user_id).delete(
            synchronize_session=False
        )
        (
            db.query(ModelSettingPresets)
            .filter(ModelSettingPresets.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(LLMGenerationStatistic)
            .filter(LLMGenerationStatistic.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(ToolCallStatistic)
            .filter(ToolCallStatistic.user_id == user_id)
            .delete(synchronize_session=False)
        )
        realtime_session_ids = [
            session_id
            for (session_id,) in db.query(RealtimeSession.session_id)
            .filter(RealtimeSession.user_id == user_id)
            .all()
        ]
        if realtime_session_ids:
            (
                db.query(LLMGenerationStatistic)
                .filter(LLMGenerationStatistic.session_id.in_(realtime_session_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(ToolCallStatistic)
                .filter(ToolCallStatistic.session_id.in_(realtime_session_ids))
                .delete(synchronize_session=False)
            )
        (
            db.query(RealtimeSession)
            .filter(RealtimeSession.user_id == user_id)
            .delete(synchronize_session=False)
        )
        personal_mcp_server_ids = [
            server_id
            for (server_id,) in db.query(MCPServer.id)
            .filter(MCPServer.owner_user_id == user_id)
            .all()
        ]
        (
            db.query(MCPOAuthState)
            .filter(MCPOAuthState.user_id == user_id)
            .delete(synchronize_session=False)
        )
        (
            db.query(MCPServer)
            .filter(MCPServer.owner_user_id == user_id)
            .delete(synchronize_session=False)
        )
        if personal_mcp_server_ids:
            from app.mcp.utils import stop_mcp_subscription_listener

            # Listener shutdown is an external side effect. Schedule it after
            # the database commit so a rollback leaves live server listeners
            # untouched, matching the other storage cleanup in this function.
            for mcp_server_id in personal_mcp_server_ids:
                post_commit_cleanup_actions.append(
                    lambda mcp_server_id=mcp_server_id: stop_mcp_subscription_listener(
                        mcp_server_id
                    )
                )

        # Delete prompts and prompt subscriptions.
        user_prompt_ids = [
            prompt_id
            for (prompt_id,) in db.query(Prompts.id)
            .filter(Prompts.user_id == user_id)
            .all()
        ]
        if user_prompt_ids:
            (
                db.query(SharedPromptSubscription)
                .filter(SharedPromptSubscription.prompt_id.in_(user_prompt_ids))
                .delete(synchronize_session=False)
            )
            (
                db.query(Prompts)
                .filter(Prompts.id.in_(user_prompt_ids))
                .delete(synchronize_session=False)
            )
        (
            db.query(SharedPromptSubscription)
            .filter(SharedPromptSubscription.subscriber_id == user_id)
            .delete(synchronize_session=False)
        )

        # Delete projects owned by the user and memberships (owned and subscribed).
        user_project_rows = (
            db.query(Project.id).filter(Project.user_id == user_id).all()
        )
        if user_project_rows:
            user_project_ids = [project_id for (project_id,) in user_project_rows]
            post_commit_cleanup_actions.extend(
                _delete_projects_and_related_data(db, user_project_ids)
            )
        (
            db.query(ProjectMember)
            .filter(ProjectMember.user_id == user_id)
            .delete(synchronize_session=False)
        )

        local_files_base = user_files_base_dir.resolve()
        local_user_files_dir = (
            local_files_base / str(user_id).strip().strip("/")
        ).resolve()
        user_files_dir = (
            local_user_files_dir
            if local_files_base in local_user_files_dir.parents
            or local_user_files_dir == local_files_base
            else None
        )
        if user_files_dir is not None:
            post_commit_cleanup_actions.append(
                lambda user_files_dir=user_files_dir: shutil.rmtree(
                    user_files_dir, ignore_errors=True
                )
            )
        db.delete(user)
        commit_started = True
        db.commit()
    except Exception:
        db.rollback()
        if erasure_record is not None and not commit_started:
            try:
                from app.users.erasure_ledger import record_cancelled_user_erasure

                record_cancelled_user_erasure(
                    user_id,
                    operation_id=erasure_record["operation_id"],
                    auth_policy=erasure_record["auth_policy"],
                    audit_policy=erasure_record["audit_policy"],
                    erased_at=erasure_record["erased_at"],
                    retention_started_at=erasure_record["retention_started_at"],
                )
            except Exception:
                # The unresolved intent is deliberately privacy-biased and will
                # be resolved against the still-present user by offline startup.
                logger.exception(
                    "Could not close a rolled-back user-erasure intent",
                    extra={"user_id": user_id},
                )
        raise

    ledger_error: Exception | None = None
    if erasure_record is not None:
        try:
            from app.users.erasure_ledger import record_completed_user_erasure

            record_completed_user_erasure(
                user_id,
                auth_policy=erasure_record["auth_policy"],
                audit_policy=erasure_record["audit_policy"],
                erased_at=erasure_record["erased_at"],
                retention_started_at=erasure_record["retention_started_at"],
                operation_id=erasure_record["operation_id"],
            )
        except Exception as exc:  # noqa: BLE001
            ledger_error = exc
            logger.exception(
                "Permanent deletion completed but its restore safeguard could not be recorded",
                extra={"user_id": user_id},
            )

    cleanup_failed = False
    for cleanup_action in post_commit_cleanup_actions:
        try:
            cleanup_action()
        except Exception:
            cleanup_failed = True
            logger.exception(
                "Failed to remove user data from storage after hard delete",
                extra={"user_id": user_id},
            )

    if cleanup_failed:
        raise HTTPException(
            status_code=500, detail="Failed to delete user data from storage"
        )
    if ledger_error is not None:
        raise HTTPException(
            status_code=500,
            detail=(
                "The account was permanently deleted, but Omlorix could not record "
                "the restore-erasure safeguard. Resolve ledger storage before restoring backups."
            ),
        ) from ledger_error

    return True
