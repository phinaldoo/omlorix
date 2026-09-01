"""Resolve memory feature policy once for a request or model run."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.groups.init import get_user_group_setting_value
from app.memories.service import MemoryScope
from app.projects.models import get_project_with_access
from app.users.init import get_user_settings


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    """The complete set of decisions needed by memory consumers."""

    user_id: str
    requested_project_id: str | None
    feature_enabled: bool
    account_enabled: bool
    include_in_context_setting: bool
    auto_create_setting: bool
    project_enabled: bool

    @property
    def active(self) -> bool:
        """Return whether memories are active for the account."""

        return self.feature_enabled and self.account_enabled

    @property
    def use_project_memory(self) -> bool:
        """Return whether the requested project has its shared scope enabled."""

        return bool(self.active and self.requested_project_id and self.project_enabled)

    @property
    def scope(self) -> MemoryScope:
        """Return the effective chat/tool scope, falling back to personal."""

        if self.use_project_memory:
            return MemoryScope.project(str(self.requested_project_id))
        return MemoryScope.personal(self.user_id)

    @property
    def include_in_context(self) -> bool:
        """Return whether saved memories should be attached to model context."""

        return self.active and self.include_in_context_setting

    @property
    def auto_create(self) -> bool:
        """Return whether the model may create a memory."""

        return self.active and self.auto_create_setting


def get_memory_settings(db, user_id: str) -> dict[str, bool]:
    """Load the complete memory settings page with one user-settings lookup."""

    page = get_user_settings(user_id, db).get("memory", {})
    return {
        "enabled": bool(page.get("enabled")),
        "include_in_context": bool(page.get("include_in_context")),
        "auto_create": bool(page.get("auto_create")),
    }


def get_memory_policy(
    db,
    user_id: str,
    project_id: str | None = None,
    *,
    project=None,
) -> MemoryPolicy:
    """Resolve account and optional project memory policy once."""

    normalized_user_id = str(user_id or "").strip()
    normalized_project_id = str(project_id or "").strip() or None
    if db is None or not normalized_user_id:
        return MemoryPolicy(
            "", normalized_project_id, False, False, False, False, False
        )

    feature_enabled = bool(
        get_user_group_setting_value(
            normalized_user_id, "memories", "enabled_memories", db
        )
    )
    settings = get_memory_settings(db, normalized_user_id)
    project_enabled = False
    if normalized_project_id and feature_enabled and settings["enabled"]:
        resolved_project = project
        if resolved_project is None:
            try:
                resolved_project = get_project_with_access(
                    db, normalized_user_id, normalized_project_id
                )
            except HTTPException:
                # Chat and tool paths may outlive project membership. Treat a
                # missing/inaccessible project as a personal-memory request.
                # Routers still raise because they resolve access first and
                # pass the successfully resolved project into this function.
                resolved_project = None
        project_enabled = bool(
            (getattr(resolved_project, "settings", None) or {}).get(
                "separate_memory_enabled"
            )
        )

    return MemoryPolicy(
        user_id=normalized_user_id,
        requested_project_id=normalized_project_id,
        feature_enabled=feature_enabled,
        account_enabled=settings["enabled"],
        include_in_context_setting=settings["include_in_context"],
        auto_create_setting=settings["auto_create"],
        project_enabled=project_enabled,
    )
