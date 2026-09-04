"""Bounded, scope-aware persistence for atomic memories and full profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import re
from typing import Any, Callable, Sequence
import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.memories.models import Memory, MemoryDeletion, MemoryProfile, MemoryState
from app.memories.schemas import (
    MEMORY_IMPORT_LIMIT_MESSAGE,
    MAX_MEMORIES_PER_SCOPE,
    MAX_MEMORY_IMPORT_ITEMS,
    MemoryExportData,
    MemoryExportItem,
    MemoryExportPayload,
    MemoryImportItem,
    MemoryCandidate,
    MemoryProfileResponse,
)


CURRENT_MEMORIES_EXPORT_VERSION = 2.0
SUPPORTED_MEMORIES_EXPORT_VERSIONS = frozenset({1.0, 2.0})
# Enforce this again at merge time: provider calls and local queues may outlive
# their admission. Deletion guards can then be removed without enabling replay.
MAX_MEMORY_SOURCE_AGE = timedelta(hours=24)
MEMORY_DELETION_RETENTION = timedelta(hours=48)
_MEMORY_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_KINDS = frozenset(
    {
        "identity",
        "preference",
        "project",
        "relationship",
        "constraint",
        "experience",
        "goal",
        "other",
    }
)
_STABILITIES = frozenset({"stable", "slow", "changing", "ephemeral"})
_SENSITIVITIES = frozenset({"normal", "sensitive", "secret"})
_DISALLOWED_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(
        r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-|AIza)"
        r"[A-Za-z0-9_-]{8,}\b"
    ),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"\b(?:password|passphrase|passcode|api[ _-]?key|access[ _-]?token|"
        r"refresh[ _-]?token|client[ _-]?secret|private[ _-]?key|"
        r"bank[ _-]?account|routing[ _-]?number|card[ _-]?number)\b"
        r"\s*(?:is|=|:)\s*[\"']?\S{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


@dataclass(frozen=True, slots=True)
class LifecyclePolicy:
    half_life_days: int
    review_after_days: int
    expire_after_days: int


LIFECYCLE_POLICIES: dict[str, LifecyclePolicy] = {
    "stable": LifecyclePolicy(540, 365, 1_095),
    "slow": LifecyclePolicy(180, 180, 540),
    "changing": LifecyclePolicy(45, 45, 180),
    "ephemeral": LifecyclePolicy(7, 7, 30),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Identify exactly one personal or project memory collection."""

    user_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        normalized_user_id = str(self.user_id or "").strip() or None
        normalized_project_id = str(self.project_id or "").strip() or None
        if (normalized_user_id is None) == (normalized_project_id is None):
            raise ValueError("MemoryScope requires exactly one user_id or project_id")
        object.__setattr__(self, "user_id", normalized_user_id)
        object.__setattr__(self, "project_id", normalized_project_id)

    @classmethod
    def personal(cls, user_id: str) -> "MemoryScope":
        return cls(user_id=user_id)

    @classmethod
    def project(cls, project_id: str) -> "MemoryScope":
        return cls(project_id=project_id)

    @property
    def is_project(self) -> bool:
        return self.project_id is not None

    def filter_expression(self, model=Memory):
        if self.project_id is not None:
            return model.project_id == self.project_id
        return model.user_id == self.user_id

    def owner_values(self) -> dict[str, str | None]:
        return {"user_id": self.user_id, "project_id": self.project_id}


def normalize_memory_content(content: str) -> str:
    normalized = " ".join(str(content or "").strip().split())
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="content is required"
        )
    if len(normalized) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="content must be 500 characters or fewer",
        )
    return normalized


def memory_content_key(content: str) -> str:
    return normalize_memory_content(content).casefold()


def normalize_memory_key(value: str | None, *, prefix: str = "manual") -> str:
    normalized = str(value or "").strip().lower()
    if normalized and _MEMORY_KEY_RE.fullmatch(normalized):
        return normalized
    if normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="memory_key must use lowercase letters, numbers, dots, dashes, or underscores",
        )
    return f"{prefix}.{uuid.uuid4().hex}"


def normalize_memory_source_date(source_date: date | str | None) -> date | None:
    if source_date is None or isinstance(source_date, date):
        return source_date
    normalized = source_date.strip().lower()
    if not normalized or normalized == "unknown":
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date must be 'unknown' or a YYYY-MM-DD string",
        ) from exc


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datetime values must be ISO formatted strings or null",
        )
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datetime values must be ISO formatted strings or null",
        ) from exc
    return as_utc(parsed)


def lifecycle_dates(
    stability: str,
    confirmed_at: datetime | None = None,
) -> tuple[datetime, datetime]:
    normalized_stability = stability if stability in _STABILITIES else "slow"
    policy = LIFECYCLE_POLICIES[normalized_stability]
    base = as_utc(confirmed_at) or utcnow()
    return (
        base + timedelta(days=policy.review_after_days),
        base + timedelta(days=policy.expire_after_days),
    )


def _scope_query(db: Session, scope: MemoryScope, *, include_inactive: bool = False):
    query = db.query(Memory).filter(scope.filter_expression())
    if not include_inactive:
        query = query.filter(Memory.status == "active")
    return query


def _active_expression(now: datetime | None = None):
    current = as_utc(now) or utcnow()
    return or_(Memory.expires_at.is_(None), Memory.expires_at > current)


def list_memories(
    db: Session,
    scope: MemoryScope,
    *,
    limit: int | None = None,
    offset: int = 0,
    now: datetime | None = None,
) -> list[Memory]:
    """List active, non-expired facts with deterministic ordering."""

    query = (
        _scope_query(db, scope)
        .filter(_active_expression(now))
        .order_by(
            Memory.importance.desc(),
            Memory.updated_at.desc(),
            Memory.created_at.desc(),
        )
    )
    if offset > 0:
        query = query.offset(offset)
    if limit is not None and limit > 0:
        query = query.limit(limit)
    return query.all()


def _get_memory(db: Session, scope: MemoryScope, memory_id: str) -> Memory:
    normalized_id = str(memory_id or "").strip()
    if not normalized_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="memory_id is required"
        )
    memory = (
        _scope_query(db, scope)
        .filter(Memory.id == normalized_id, _active_expression())
        .first()
    )
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found"
        )
    return memory


def _lock_scope_owner(db: Session, scope: MemoryScope, *, skip_locked: bool = False) -> bool:
    """Serialize cap-sensitive changes on an existing owner row."""

    if scope.user_id:
        from app.users.models import User

        owner = db.query(User.id).filter(User.id == scope.user_id)
    else:
        from app.projects.models import Project

        owner = db.query(Project.id).filter(Project.id == scope.project_id)
    return owner.with_for_update(skip_locked=skip_locked).first() is not None


def count_active_memories(db: Session, scope: MemoryScope) -> int:
    return int(
        _scope_query(db, scope).filter(_active_expression()).count()
    )


def _ensure_capacity(db: Session, scope: MemoryScope, *, additional: int = 1) -> None:
    if count_active_memories(db, scope) + max(0, int(additional)) > MAX_MEMORIES_PER_SCOPE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A memory collection can contain at most {MAX_MEMORIES_PER_SCOPE} facts",
        )


def _profile_rows(db: Session, user_id: str, *, now: datetime | None = None) -> list[Memory]:
    return list_memories(
        db,
        MemoryScope.personal(user_id),
        limit=MAX_MEMORIES_PER_SCOPE,
        now=now,
    )


def _lifecycle_state(memory: Memory, *, now: datetime | None = None) -> str:
    review_at = as_utc(memory.review_at)
    return "review" if review_at is not None and review_at <= (as_utc(now) or utcnow()) else "fresh"


def build_memory_profile_text(
    memories: Sequence[Memory], *, now: datetime | None = None
) -> str:
    """Render every bounded fact into one timestamp-aware profile document."""

    lines: list[str] = []
    for memory in memories:
        content = str(memory.content or "").strip()
        if not content:
            continue
        confirmed = as_utc(memory.last_confirmed_at or memory.updated_at)
        confirmed_label = confirmed.date().isoformat() if confirmed else "unknown"
        state = _lifecycle_state(memory, now=now)
        lines.append(
            f"- [{memory.kind}; confirmed {confirmed_label}; {state}] {content}"
        )
    return "\n".join(lines)


def rebuild_memory_profile(
    db: Session,
    user_id: str,
    *,
    source_message_id: str | None = None,
    now: datetime | None = None,
) -> MemoryProfile:
    """Materialize the complete current profile without a second model call."""

    current = as_utc(now) or utcnow()
    state = _memory_state(db, user_id)
    state.facts_revision = int(state.facts_revision or 0) + 1
    rows = _profile_rows(db, user_id, now=current)
    content = build_memory_profile_text(rows, now=current)
    fact_versions = [
        {"memory_id": str(memory.id), "version": int(memory.version or 1)}
        for memory in rows
    ]
    review_count = sum(_lifecycle_state(memory, now=current) == "review" for memory in rows)
    transition_candidates: list[datetime] = []
    for memory in rows:
        review_at = as_utc(memory.review_at)
        expires_at = as_utc(memory.expires_at)
        if review_at is not None and review_at > current:
            transition_candidates.append(review_at)
        elif expires_at is not None and expires_at > current:
            transition_candidates.append(expires_at)
    next_transition_at = min(transition_candidates, default=None)
    profile = (
        db.query(MemoryProfile).filter(MemoryProfile.user_id == user_id).first()
    )
    if profile is None:
        profile = MemoryProfile(
            user_id=user_id,
            content=content,
            version=1 if rows else 0,
            fact_versions=fact_versions,
            active_fact_count=len(rows),
            review_fact_count=review_count,
            next_transition_at=next_transition_at,
            source_revision=state.facts_revision,
            created_at=current,
            updated_at=current,
        )
        db.add(profile)
        return profile

    changed = profile.content != content or profile.fact_versions != fact_versions
    if changed:
        profile.content = content
        profile.fact_versions = fact_versions
        profile.active_fact_count = len(rows)
        profile.review_fact_count = review_count
        profile.version = int(profile.version or 0) + 1
        profile.updated_at = current
    profile.next_transition_at = next_transition_at
    profile.source_revision = state.facts_revision
    return profile


def get_memory_profile(db: Session, user_id: str) -> MemoryProfileResponse:
    """Return a read-only, expiry-safe view of the full personal profile."""

    rows = _profile_rows(db, user_id)
    current = utcnow()
    current_content = build_memory_profile_text(rows, now=current)
    review_count = sum(_lifecycle_state(memory, now=current) == "review" for memory in rows)
    profile = db.query(MemoryProfile).filter(MemoryProfile.user_id == user_id).first()
    state = db.get(MemoryState, user_id)
    run_status = state.last_run_status if state else None
    error_code = state.last_error_code if state else None
    last_run_at = as_utc(state.last_run_at) if state else None
    if (
        run_status == "processing"
        and last_run_at is not None
        and last_run_at <= current - timedelta(minutes=15)
    ):
        # A process can be terminated after marking a job as started. Do not
        # leave the user-facing state spinning forever; the next message will
        # still enqueue and supersede this stale run normally.
        run_status = "failed"
        error_code = "memory_update_interrupted"
    return MemoryProfileResponse(
        content=current_content,
        version=int(profile.version or 0) if profile else 0,
        active_fact_count=len(rows),
        review_fact_count=review_count,
        updated_at=profile.updated_at if profile else None,
        last_run_at=last_run_at,
        last_run_status=run_status,
        last_error_code=error_code,
    )


def _memory_state(db: Session, user_id: str) -> MemoryState:
    # Writers hold the scope owner lock before creating or updating this row.
    state = db.get(MemoryState, user_id)
    if state is None:
        state = MemoryState(user_id=user_id, facts_revision=0)
        db.add(state)
        db.flush()
    return state


def set_memory_run_status(
    db: Session,
    user_id: str,
    *,
    source_message_id: str,
    source_at: datetime,
    run_status: str,
    error_code: str | None = None,
    commit: bool = False,
) -> MemoryState:
    current = utcnow()
    _lock_scope_owner(db, MemoryScope.personal(user_id))
    state = _memory_state(db, user_id)
    normalized_source_at = as_utc(source_at) or current
    previous_source_at = as_utc(state.last_source_at)
    if previous_source_at is None or normalized_source_at >= previous_source_at:
        state.last_processed_message_id = source_message_id
        state.last_source_at = normalized_source_at
        state.last_run_status = run_status
        state.last_error_code = error_code
        state.last_run_at = current
    if commit:
        db.commit()
        db.refresh(state)
    return state


def create_memory(
    db: Session,
    scope: MemoryScope,
    content: str,
    *,
    source_date: date | str | None = None,
    memory_key: str | None = None,
    kind: str = "other",
    stability: str = "slow",
    importance: int = 3,
    confidence: float = 1.0,
    sensitivity: str = "normal",
    source_message_id: str | None = None,
    source_excerpt: str | None = None,
    evidence_at: datetime | None = None,
    before_commit: Callable[[Memory, bool], None] | None = None,
) -> tuple[Memory, bool]:
    """Create or explicitly reconfirm one fact under the hard 100-fact cap."""

    normalized_content = normalize_memory_content(content)
    content_key = normalized_content.casefold()
    normalized_source_date = normalize_memory_source_date(source_date)
    normalized_kind = kind if kind in _KINDS else "other"
    normalized_stability = stability if stability in _STABILITIES else "slow"
    normalized_sensitivity = sensitivity if sensitivity in _SENSITIVITIES else "normal"
    current = utcnow()
    confirmed_at = as_utc(evidence_at) or current
    review_at, expires_at = lifecycle_dates(normalized_stability, confirmed_at)
    _lock_scope_owner(db, scope)

    existing = _scope_query(db, scope).filter(Memory.content_key == content_key).first()
    if existing is not None:
        existing.version = int(existing.version or 1) + 1
        existing.last_confirmed_at = max(
            as_utc(existing.last_confirmed_at) or confirmed_at,
            confirmed_at,
        )
        existing.evidence_at = max(as_utc(existing.evidence_at) or confirmed_at, confirmed_at)
        existing.review_at, existing.expires_at = lifecycle_dates(
            str(existing.stability or normalized_stability), existing.last_confirmed_at
        )
        existing.updated_at = current
        if normalized_source_date is not None and existing.source_date is None:
            existing.source_date = normalized_source_date
        if source_message_id:
            existing.source_message_id = source_message_id
        if source_excerpt:
            existing.source_excerpt = normalize_memory_content(source_excerpt)[:500]
        if scope.user_id:
            rebuild_memory_profile(db, scope.user_id, source_message_id=source_message_id)
        try:
            if before_commit is not None:
                before_commit(existing, False)
            db.commit()
            db.refresh(existing)
        except Exception:
            db.rollback()
            raise
        return existing, False

    _ensure_capacity(db, scope)
    memory = Memory(
        id=str(uuid.uuid4()),
        content=normalized_content,
        content_key=content_key,
        memory_key=normalize_memory_key(memory_key),
        kind=normalized_kind,
        stability=normalized_stability,
        importance=max(1, min(int(importance), 5)),
        confidence=max(0.0, min(float(confidence), 1.0)),
        sensitivity=normalized_sensitivity,
        status="active",
        version=1,
        source_date=normalized_source_date,
        source_message_id=source_message_id,
        source_excerpt=(str(source_excerpt or "").strip()[:500] or None),
        evidence_at=confirmed_at,
        last_confirmed_at=confirmed_at,
        review_at=review_at,
        expires_at=expires_at,
        created_at=current,
        updated_at=current,
        **scope.owner_values(),
    )
    db.add(memory)
    try:
        db.flush()
        if scope.user_id:
            rebuild_memory_profile(db, scope.user_id, source_message_id=source_message_id)
        if before_commit is not None:
            before_commit(memory, True)
        db.commit()
        db.refresh(memory)
    except IntegrityError:
        db.rollback()
        existing = _scope_query(db, scope).filter(Memory.content_key == content_key).first()
        if existing is None:
            raise
        return create_memory(
            db,
            scope,
            normalized_content,
            source_date=normalized_source_date,
            memory_key=memory_key,
            kind=normalized_kind,
            stability=normalized_stability,
            importance=importance,
            confidence=confidence,
            sensitivity=normalized_sensitivity,
            source_message_id=source_message_id,
            source_excerpt=source_excerpt,
            evidence_at=confirmed_at,
            before_commit=before_commit,
        )
    except Exception:
        db.rollback()
        raise
    return memory, True


def update_memory(
    db: Session,
    scope: MemoryScope,
    memory_id: str,
    content: str | None,
    *,
    stability: str | None = None,
    importance: int | None = None,
) -> Memory:
    _lock_scope_owner(db, scope)
    memory = _get_memory(db, scope, memory_id)
    if content is None and stability is None and importance is None:
        return memory
    current = utcnow()
    if content is not None:
        normalized_content = normalize_memory_content(content)
        content_key = normalized_content.casefold()
        duplicate = (
            _scope_query(db, scope)
            .filter(Memory.content_key == content_key, Memory.id != memory.id)
            .first()
        )
        if duplicate is not None:
            _record_memory_deletion(db, memory, deleted_at=current)
            db.delete(memory)
            memory = duplicate
        else:
            memory.content = normalized_content
            memory.content_key = content_key
            memory.source_excerpt = normalized_content[:500]
            memory.source_message_id = None
    if stability in _STABILITIES:
        memory.stability = stability
    if importance is not None:
        memory.importance = max(1, min(int(importance), 5))
    memory.confidence = 1.0
    memory.version = int(memory.version or 1) + 1
    memory.evidence_at = current
    memory.last_confirmed_at = current
    memory.review_at, memory.expires_at = lifecycle_dates(memory.stability, current)
    memory.updated_at = current
    if scope.user_id:
        db.flush()
        rebuild_memory_profile(db, scope.user_id)
    db.commit()
    db.refresh(memory)
    return memory


def confirm_memory(db: Session, scope: MemoryScope, memory_id: str) -> Memory:
    _lock_scope_owner(db, scope)
    memory = _get_memory(db, scope, memory_id)
    current = utcnow()
    memory.confidence = 1.0
    memory.version = int(memory.version or 1) + 1
    memory.evidence_at = current
    memory.last_confirmed_at = current
    memory.review_at, memory.expires_at = lifecycle_dates(memory.stability, current)
    memory.updated_at = current
    if scope.user_id:
        rebuild_memory_profile(db, scope.user_id)
    db.commit()
    db.refresh(memory)
    return memory


def delete_memory(
    db: Session, scope: MemoryScope, memory_id: str
) -> dict[str, str | bool]:
    _lock_scope_owner(db, scope)
    memory = _get_memory(db, scope, memory_id)
    _record_memory_deletion(db, memory, deleted_at=utcnow())
    db.delete(memory)
    db.flush()
    if scope.user_id:
        rebuild_memory_profile(db, scope.user_id)
    db.commit()
    return {"deleted": True, "memory_id": memory.id}


def _record_memory_deletion(db: Session, memory: Memory, *, deleted_at: datetime) -> None:
    """Called under the scope-owner lock, in the same transaction as deletion."""

    db.add(MemoryDeletion(
        memory_id=memory.id,
        user_id=memory.user_id,
        project_id=memory.project_id,
        memory_key=memory.memory_key,
        version=int(memory.version or 1) + 1,
        deleted_at=deleted_at,
    ))


def normalize_model_memory_key(value: str, *, kind: str, content: str) -> str:
    """Turn a model-proposed semantic slot into a stable, index-safe key."""

    normalized = re.sub(r"[^a-z0-9_.-]+", ".", str(value or "").casefold())
    normalized = re.sub(r"\.{2,}", ".", normalized).strip("._-")
    if not normalized:
        digest = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()[:20]
        normalized = f"{kind}.{digest}"
    if not normalized[0].isalnum():
        normalized = f"{kind}.{normalized}"
    return normalized[:120].rstrip("._-")


def _memory_retention_score(memory: Memory) -> float:
    """Rank facts only when the hard cap requires a replacement decision."""

    manual_bonus = 1.0 if not memory.source_message_id else 0.0
    review_penalty = 1.0 if _lifecycle_state(memory) == "review" else 0.0
    return (
        (max(1, min(int(memory.importance or 3), 5)) * 2.0)
        + max(0.0, min(float(memory.confidence or 0.0), 1.0))
        + float(memory.freshness)
        + manual_bonus
        - review_penalty
    )


def _candidate_content(candidate: MemoryCandidate) -> str:
    return str(candidate.content or "").strip()


def _candidate_source_excerpt(candidate: MemoryCandidate, source_text: str) -> str:
    evidence = " ".join(str(candidate.evidence or "").strip().split())
    normalized_source = " ".join(str(source_text or "").strip().split())
    if evidence and evidence.casefold() in normalized_source.casefold():
        return evidence[:500]
    return normalized_source[:500]


def _contains_disallowed_secret(*values: str) -> bool:
    """Reject common credential material even if the memory model mislabels it."""

    combined = "\n".join(str(value or "") for value in values)
    return any(pattern.search(combined) for pattern in _DISALLOWED_SECRET_PATTERNS)


def apply_memory_consolidation(
    db: Session,
    *,
    user_id: str,
    source_message_id: str,
    source_at: datetime,
    source_text: str,
    candidates: Sequence[MemoryCandidate],
) -> dict[str, int | str]:
    """Apply one model extraction atomically under the per-user cap.

    The source timestamp is authoritative. An older background job can add a
    missing historical fact, but it cannot overwrite, confirm, or forget a
    semantic slot that has newer evidence.
    """

    from app.logging.models import stage_audit_log_event

    scope = MemoryScope.personal(user_id)
    _lock_scope_owner(db, scope)
    current = utcnow()
    evidence_at = as_utc(source_at) or current
    if evidence_at <= current - MAX_MEMORY_SOURCE_AGE:
        # Release the scope lock even for an expired invocation.
        db.rollback()
        return {"status": "skipped", "reason": "source_expired"}

    candidates = candidates[:MAX_MEMORIES_PER_SCOPE]
    candidate_keys = {
        normalize_model_memory_key(
            candidate.key, kind=candidate.kind,
            content=_candidate_content(candidate) or candidate.evidence,
        )
        for candidate in candidates
    }
    target_ids = {str(candidate.target_memory_id) for candidate in candidates if candidate.target_memory_id}
    # Only fetch guards relevant to this bounded batch. Old target IDs remain
    # protected even when the model proposes a different semantic key.
    guards = (
        db.query(MemoryDeletion)
        .filter(
            scope.filter_expression(MemoryDeletion),
            MemoryDeletion.deleted_at >= evidence_at,
        )
    )
    deleted_keys = {
        row.memory_key for row in guards
        .filter(MemoryDeletion.memory_key.in_(candidate_keys))
        .with_entities(MemoryDeletion.memory_key).distinct().all()
    }
    deleted_ids = {
        row.memory_id for row in guards
        .filter(MemoryDeletion.memory_id.in_(target_ids))
        .with_entities(MemoryDeletion.memory_id).all()
    }

    rows = _scope_query(db, scope).order_by(Memory.created_at.asc()).all()
    active_rows: list[Memory] = []
    deleted_count = 0
    for row in rows:
        expires_at = as_utc(row.expires_at)
        if expires_at is not None and expires_at <= current:
            db.delete(row)
            deleted_count += 1
        else:
            active_rows.append(row)

    by_id = {str(row.id): row for row in active_rows}
    by_key = {str(row.memory_key): row for row in active_rows}
    by_content = {str(row.content_key): row for row in active_rows}
    created_count = 0
    updated_count = 0
    confirmed_count = 0
    skipped_count = 0
    stale_count = 0
    evicted_count = 0
    processed_slots: set[str] = set()

    def forget(row: Memory, *, deleted_at: datetime = current) -> None:
        nonlocal deleted_count
        _record_memory_deletion(db, row, deleted_at=deleted_at)
        deleted_keys.add(str(row.memory_key))
        deleted_ids.add(str(row.id))
        db.delete(row)
        by_id.pop(str(row.id), None)
        by_key.pop(str(row.memory_key), None)
        by_content.pop(str(row.content_key), None)
        if row in active_rows:
            active_rows.remove(row)
        deleted_count += 1

    def newer_than_source(row: Memory) -> bool:
        row_evidence_at = as_utc(row.evidence_at)
        return bool(row_evidence_at is not None and row_evidence_at > evidence_at)

    def refresh_evidence(row: Memory, candidate: MemoryCandidate) -> None:
        row.kind = candidate.kind
        row.stability = candidate.stability
        row.importance = max(1, min(int(candidate.importance), 5))
        row.confidence = max(0.0, min(float(candidate.confidence), 1.0))
        row.sensitivity = candidate.sensitivity
        row.source_date = evidence_at.date()
        row.source_message_id = source_message_id
        row.source_excerpt = _candidate_source_excerpt(candidate, source_text)
        row.evidence_at = evidence_at
        row.last_confirmed_at = evidence_at
        row.review_at, row.expires_at = lifecycle_dates(row.stability, evidence_at)
        row.version = int(row.version or 1) + 1
        row.updated_at = current

    for candidate in candidates[:MAX_MEMORIES_PER_SCOPE]:
        if float(candidate.confidence) < 0.45:
            skipped_count += 1
            continue

        proposed_content = _candidate_content(candidate)
        key = normalize_model_memory_key(
            candidate.key,
            kind=candidate.kind,
            content=proposed_content or candidate.evidence,
        )
        target_id = str(candidate.target_memory_id or "").strip()
        if key in deleted_keys or target_id in deleted_ids:
            stale_count += 1
            continue
        target = by_id.get(target_id) if target_id else None
        if target is None:
            target = by_key.get(key)

        # Retractions must remain possible even when their evidence repeats a
        # credential. All other actions get a deterministic safety check in
        # addition to the model-provided sensitivity classification.
        if candidate.action != "forget" and (
            candidate.sensitivity == "secret"
            or _contains_disallowed_secret(proposed_content, candidate.evidence)
        ):
            skipped_count += 1
            continue

        if target is not None and newer_than_source(target):
            stale_count += 1
            continue

        operation_slot = str(target.memory_key) if target is not None else key
        if operation_slot in processed_slots:
            skipped_count += 1
            continue
        processed_slots.add(operation_slot)

        if candidate.action == "forget":
            if target is None:
                # A retraction may finish before the earlier create job. The
                # semantic slot still needs a guard, without any fact body.
                db.add(MemoryDeletion(
                    memory_id=str(uuid.uuid4()), user_id=user_id,
                    memory_key=key, version=1, deleted_at=evidence_at,
                ))
                deleted_keys.add(key)
                skipped_count += 1
                continue
            forget(target, deleted_at=evidence_at)
            continue

        if candidate.action == "confirm":
            if target is None:
                skipped_count += 1
                continue
            refresh_evidence(target, candidate)
            confirmed_count += 1
            continue

        try:
            normalized_content = normalize_memory_content(proposed_content)
        except HTTPException:
            skipped_count += 1
            continue
        content_key = memory_content_key(normalized_content)
        duplicate = by_content.get(content_key)
        if duplicate is not None and duplicate is not target:
            if newer_than_source(duplicate):
                stale_count += 1
                continue
            if target is not None:
                forget(target)
            refresh_evidence(duplicate, candidate)
            confirmed_count += 1
            continue

        if target is not None:
            by_content.pop(str(target.content_key), None)
            target.content = normalized_content
            target.content_key = content_key
            refresh_evidence(target, candidate)
            by_content[content_key] = target
            updated_count += 1
            continue

        # New facts compete only when the collection is already full. This
        # avoids silently discarding user-confirmed memories merely because a
        # low-value extraction arrived later.
        if len(active_rows) >= MAX_MEMORIES_PER_SCOPE:
            weakest = min(active_rows, key=_memory_retention_score)
            candidate_score = (int(candidate.importance) * 2.0) + float(candidate.confidence)
            if int(candidate.importance) < 4 or candidate_score <= _memory_retention_score(weakest):
                skipped_count += 1
                continue
            forget(weakest)
            evicted_count += 1

        review_at, expires_at = lifecycle_dates(candidate.stability, evidence_at)
        memory = Memory(
            id=str(uuid.uuid4()),
            user_id=user_id,
            project_id=None,
            content=normalized_content,
            content_key=content_key,
            memory_key=key,
            kind=candidate.kind,
            stability=candidate.stability,
            importance=max(1, min(int(candidate.importance), 5)),
            confidence=max(0.0, min(float(candidate.confidence), 1.0)),
            sensitivity=candidate.sensitivity,
            status="active",
            version=1,
            source_date=evidence_at.date(),
            source_message_id=source_message_id,
            source_excerpt=_candidate_source_excerpt(candidate, source_text),
            evidence_at=evidence_at,
            last_confirmed_at=evidence_at,
            review_at=review_at,
            expires_at=expires_at,
            created_at=current,
            updated_at=current,
        )
        db.add(memory)
        active_rows.append(memory)
        by_id[memory.id] = memory
        by_key[memory.memory_key] = memory
        by_content[memory.content_key] = memory
        created_count += 1

    db.flush()
    changed_count = created_count + updated_count + confirmed_count + deleted_count
    rebuild_memory_profile(db, user_id, now=current)
    set_memory_run_status(
        db, user_id, source_message_id=source_message_id,
        source_at=evidence_at, run_status="updated" if changed_count else "unchanged",
    )

    stage_audit_log_event(
        db,
        user_id=user_id,
        action="MEMORY_CONSOLIDATED",
        details={
            "source": "memory_model",
            "source_message_id": source_message_id,
            "created_count": created_count,
            "updated_count": updated_count,
            "confirmed_count": confirmed_count,
            "deleted_count": deleted_count,
            "evicted_count": evicted_count,
            "skipped_count": skipped_count,
            "stale_count": stale_count,
        },
        user_agent="omlorix-memory-worker",
        category="memories",
    )
    db.commit()
    return {
        "status": "updated" if changed_count else "unchanged",
        "created_count": created_count,
        "updated_count": updated_count,
        "confirmed_count": confirmed_count,
        "deleted_count": deleted_count,
        "evicted_count": evicted_count,
        "skipped_count": skipped_count,
        "stale_count": stale_count,
    }


def export_memories(db: Session, scope: MemoryScope) -> dict[str, Any]:
    items = [
        MemoryExportItem(
            content=memory.content,
            memory_key=memory.memory_key,
            kind=memory.kind,
            stability=memory.stability,
            importance=memory.importance,
            confidence=memory.confidence,
            sensitivity=memory.sensitivity,
            version=memory.version,
            source_excerpt=memory.source_excerpt,
            source_date=memory.source_date.isoformat() if memory.source_date else None,
            evidence_at=memory.evidence_at.isoformat() if memory.evidence_at else None,
            last_confirmed_at=(
                memory.last_confirmed_at.isoformat() if memory.last_confirmed_at else None
            ),
            review_at=memory.review_at.isoformat() if memory.review_at else None,
            expires_at=memory.expires_at.isoformat() if memory.expires_at else None,
            created_at=memory.created_at.isoformat() if memory.created_at else None,
            updated_at=memory.updated_at.isoformat() if memory.updated_at else None,
        )
        for memory in list_memories(db, scope, limit=MAX_MEMORIES_PER_SCOPE)
    ]
    return MemoryExportPayload(
        export_type="memories",
        export_version=CURRENT_MEMORIES_EXPORT_VERSION,
        data=MemoryExportData(memories=items),
    ).model_dump(mode="json")


def _entry_values(entry: MemoryImportItem | MemoryExportItem) -> dict[str, Any]:
    content = normalize_memory_content(entry.content)
    source_value = entry.date if isinstance(entry, MemoryImportItem) else entry.source_date
    source_date = normalize_memory_source_date(source_value)
    source_datetime = (
        datetime.combine(source_date, datetime_time.min, tzinfo=timezone.utc)
        if source_date
        else None
    )
    if isinstance(entry, MemoryImportItem):
        return {
            "content": content,
            "content_key": content.casefold(),
            "source_date": source_date,
            "memory_key": None,
            "kind": "other",
            "stability": "slow",
            "importance": 3,
            "confidence": 1.0,
            "sensitivity": "normal",
            "version": 1,
            "source_excerpt": content[:500],
            "evidence_at": source_datetime,
            "last_confirmed_at": source_datetime,
            "review_at": None,
            "expires_at": None,
            "created_at": source_datetime,
            "updated_at": source_datetime,
        }
    return {
        "content": content,
        "content_key": content.casefold(),
        "source_date": source_date,
        "memory_key": entry.memory_key,
        "kind": entry.kind or "other",
        "stability": entry.stability or "slow",
        "importance": entry.importance or 3,
        "confidence": entry.confidence if entry.confidence is not None else 1.0,
        "sensitivity": entry.sensitivity or "normal",
        "version": entry.version or 1,
        "source_excerpt": entry.source_excerpt or content[:500],
        "evidence_at": _parse_iso_datetime(entry.evidence_at) or source_datetime,
        "last_confirmed_at": _parse_iso_datetime(entry.last_confirmed_at) or source_datetime,
        "review_at": _parse_iso_datetime(entry.review_at),
        "expires_at": _parse_iso_datetime(entry.expires_at),
        "created_at": _parse_iso_datetime(entry.created_at),
        "updated_at": _parse_iso_datetime(entry.updated_at),
    }


def import_memories(
    db: Session,
    scope: MemoryScope,
    entries: Sequence[MemoryImportItem | MemoryExportItem],
    *,
    _retry_on_conflict: bool = True,
) -> dict[str, Any]:
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one memory is required",
        )
    if len(entries) > MAX_MEMORY_IMPORT_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=MEMORY_IMPORT_LIMIT_MESSAGE
        )

    now = utcnow()
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        values = _entry_values(entry)
        confirmed_at = as_utc(values["last_confirmed_at"] or values["evidence_at"]) or now
        stability = values["stability"] if values["stability"] in _STABILITIES else "slow"
        review_at, expires_at = lifecycle_dates(stability, confirmed_at)
        if values["review_at"] is not None:
            review_at = values["review_at"]
        if values["expires_at"] is not None:
            expires_at = values["expires_at"]
        # A restored archive must not resurrect a fact whose lifecycle had
        # already ended. Interactive imports have no explicit expiration and
        # therefore continue to receive a fresh lifecycle from their source.
        if as_utc(expires_at) is not None and as_utc(expires_at) <= now:
            continue
        values["_confirmed_at"] = confirmed_at
        values["_stability"] = stability
        values["_review_at"] = review_at
        values["_expires_at"] = expires_at
        normalized.append(values)

    _lock_scope_owner(db, scope)
    expired_rows = (
        _scope_query(db, scope)
        .filter(Memory.expires_at.isnot(None), Memory.expires_at <= now)
        .all()
    )
    for expired_row in expired_rows:
        db.delete(expired_row)
    if expired_rows:
        db.flush()
    current_rows = list_memories(db, scope, limit=MAX_MEMORIES_PER_SCOPE + 1)
    by_content = {row.content_key: row for row in current_rows}
    by_key = {row.memory_key: row for row in current_rows}
    unique_new_content = {
        item["content_key"]
        for item in normalized
        if item["content_key"] not in by_content
        and not (item["memory_key"] and item["memory_key"] in by_key)
    }
    _ensure_capacity(db, scope, additional=len(unique_new_content))

    created_count = 0
    imported_items: list[Memory] = []
    for values in normalized:
        memory = by_content.get(values["content_key"])
        if memory is None and values["memory_key"]:
            memory = by_key.get(str(values["memory_key"]).lower())
        confirmed_at = values["_confirmed_at"]
        stability = values["_stability"]
        review_at = values["_review_at"]
        expires_at = values["_expires_at"]
        if memory is None:
            memory = Memory(
                id=str(uuid.uuid4()),
                content=values["content"],
                content_key=values["content_key"],
                memory_key=normalize_memory_key(values["memory_key"], prefix="import"),
                kind=values["kind"] if values["kind"] in _KINDS else "other",
                stability=stability,
                importance=max(1, min(int(values["importance"]), 5)),
                confidence=max(0.0, min(float(values["confidence"]), 1.0)),
                sensitivity=(
                    values["sensitivity"]
                    if values["sensitivity"] in _SENSITIVITIES
                    else "normal"
                ),
                status="active",
                version=max(1, int(values["version"])),
                source_date=values["source_date"],
                source_excerpt=str(values["source_excerpt"] or "")[:500] or None,
                evidence_at=as_utc(values["evidence_at"]) or confirmed_at,
                last_confirmed_at=confirmed_at,
                review_at=review_at,
                expires_at=expires_at,
                created_at=as_utc(values["created_at"]) or now,
                updated_at=as_utc(values["updated_at"] or values["created_at"]) or now,
                **scope.owner_values(),
            )
            db.add(memory)
            by_content[memory.content_key] = memory
            by_key[memory.memory_key] = memory
            created_count += 1
        elif values["source_date"] is not None and memory.source_date is None:
            memory.source_date = values["source_date"]
        imported_items.append(memory)

    try:
        db.flush()
        if scope.user_id:
            rebuild_memory_profile(db, scope.user_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        if not _retry_on_conflict:
            raise
        return import_memories(db, scope, entries, _retry_on_conflict=False)

    return {
        "total_received": len(entries),
        "created_count": created_count,
        "deduped_count": len(entries) - created_count,
        "items": imported_items,
    }


def import_memory_export(
    db: Session,
    scope: MemoryScope,
    payload: MemoryExportPayload,
) -> dict[str, Any]:
    if payload.export_version not in SUPPORTED_MEMORIES_EXPORT_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export_version '{payload.export_version}'. "
                f"Expected one of {sorted(SUPPORTED_MEMORIES_EXPORT_VERSIONS)}."
            ),
        )
    return import_memories(db, scope, payload.data.memories)


def sweep_expired_memories(
    db: Session,
    *,
    user_id: str | None = None,
    batch_size: int = 1_000,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Hard-delete expired facts in bounded batches and refresh profiles."""

    current = as_utc(now) or utcnow()
    query = db.query(Memory).filter(
        Memory.status == "active",
        Memory.expires_at.isnot(None),
        Memory.expires_at <= current,
    )
    if user_id:
        query = query.filter(Memory.user_id == str(user_id))
    query = query.order_by(Memory.expires_at.asc(), Memory.id.asc()).limit(
        max(1, min(int(batch_size), 5_000))
    )
    candidates = query.with_entities(Memory.id, Memory.user_id, Memory.project_id).all()
    if not candidates:
        return 0
    scopes: dict[tuple[str, str], list[str]] = {}
    for row in candidates:
        scopes.setdefault((row.user_id or "", row.project_id or ""), []).append(row.id)
    removed = 0
    for (owner_id, project_id), ids in sorted(scopes.items()):
        scope = MemoryScope(user_id=owner_id or None, project_id=project_id or None)
        # All fact/projection writers lock the owner first. Do not retain a
        # fact-row lock while waiting on an interactive edit or deletion.
        if not _lock_scope_owner(db, scope, skip_locked=True):
            continue
        rows = db.query(Memory).filter(
            Memory.id.in_(ids), Memory.status == "active", Memory.expires_at <= current,
        ).populate_existing().all()
        for row in rows:
            db.delete(row)
        removed += len(rows)
        db.flush()
        if rows and owner_id:
            rebuild_memory_profile(db, owner_id, now=current)
    if commit:
        db.commit()
    return removed


def sweep_memory_deletions(db: Session, *, batch_size: int = 1_000) -> int:
    """Remove guards only once every source they protect is inadmissibly old."""

    rows = (
        db.query(MemoryDeletion)
        .filter(MemoryDeletion.deleted_at < utcnow() - MEMORY_DELETION_RETENTION)
        .order_by(MemoryDeletion.deleted_at, MemoryDeletion.memory_id)
        .limit(max(1, min(int(batch_size), 5_000)))
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)


def refresh_due_memory_profiles(
    db: Session,
    *,
    batch_size: int = 1_000,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Rematerialize profiles whose displayed lifecycle state crossed a boundary."""

    current = as_utc(now) or utcnow()
    query = (
        db.query(MemoryProfile)
        .filter(
            or_(MemoryProfile.source_revision.is_(None),
                MemoryProfile.next_transition_at <= current),
        )
        .order_by(MemoryProfile.next_transition_at.asc(), MemoryProfile.user_id.asc())
        .limit(max(1, min(int(batch_size), 5_000)))
    )
    owner_ids = sorted(row.user_id for row in query.with_entities(MemoryProfile.user_id).all())
    refreshed = 0
    for owner_id in owner_ids:
        if not _lock_scope_owner(db, MemoryScope.personal(owner_id), skip_locked=True):
            continue
        # Read facts only after the owner lock, so an old maintenance snapshot
        # cannot put deleted content back into the materialized profile.
        rebuild_memory_profile(db, owner_id, now=current)
        refreshed += 1
    if owner_ids and commit:
        db.commit()
    return refreshed
