import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from app.projects import models as project_models


class _FakeQuery:
    def __init__(self, result=None, count_result=0):
        self._result = result
        self._count_result = count_result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if isinstance(self._result, list):
            if self._result:
                return self._result.pop(0)
            return None
        return self._result

    def count(self):
        return self._count_result


class _FakeDb:
    def __init__(self, project=None, member=None, member_count=0, commit_exception=None):
        self.project = project
        self.member = member
        self.member_count = member_count
        self.commits = 0
        self.rollbacks = 0
        self.commit_exception = commit_exception

    def query(self, model):
        if model is project_models.ProjectMember:
            return _FakeQuery(self.member, count_result=self.member_count)
        return _FakeQuery(self.project)

    def add(self, _item):
        pass

    def commit(self):
        self.commits += 1
        if self.commit_exception is not None:
            exc = self.commit_exception
            self.commit_exception = None
            raise exc

    def refresh(self, _item):
        pass

    def rollback(self):
        self.rollbacks += 1


class _SequentialMemberDb(_FakeDb):
    def __init__(self, *, project=None, member_results=None, member_count=0, commit_exception=None):
        super().__init__(
            project=project,
            member=None,
            member_count=member_count,
            commit_exception=commit_exception,
        )
        self.member_results = list(member_results or [])

    def query(self, model):
        if model is project_models.ProjectMember:
            return _FakeQuery(self.member_results, count_result=self.member_count)
        return _FakeQuery(self.project)


def _project(**overrides):
    values = {
        "id": "project-1",
        "user_id": "owner-1",
        "title": "Shared project",
        "link_share_id": "share-1",
        "link_share_password_hash": None,
        "link_share_expires_at": None,
        "link_share_created_at": None,
        "created_at": datetime.now(timezone.utc),
        "last_updated_at": None,
        "settings": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ProjectShareSecurityTests:
    def setup_method(self):
        project_models._PROJECT_SHARE_PASSWORD_ATTEMPTS.clear()

    def test_create_project_link_share_requires_project_sharing_permission(self):
        db = _FakeDb(project=_project(link_share_id=None))

        with patch.object(
            project_models,
            "ensure_project_sharing_allowed",
            side_effect=HTTPException(status_code=403, detail="Project sharing is disabled for your group"),
        ):
            with pytest.raises(HTTPException) as blocked:
                project_models.create_project_link_share(db, "owner-1", "project-1")

        assert blocked.value.status_code == 403

    def test_existing_project_link_share_remains_manageable_when_project_sharing_disabled(self):
        project = _project(link_share_id="share-1")
        db = _FakeDb(project=project)

        with patch.object(
            project_models,
            "ensure_project_sharing_allowed",
            side_effect=HTTPException(status_code=403, detail="Project sharing is disabled for your group"),
        ), patch.object(project_models, "get_public_url", return_value="https://chat.example"):
            result = project_models.create_project_link_share(db, "owner-1", "project-1")

        assert result["share_id"] == "share-1"
        assert result["share_url"] == "https://chat.example/projects/join/share-1"

    def test_member_shared_project_can_restore_link_when_project_sharing_disabled(self):
        project = _project(link_share_id=None)
        db = _FakeDb(project=project, member_count=1)

        with patch.object(
            project_models,
            "ensure_project_sharing_allowed",
            side_effect=HTTPException(status_code=403, detail="Project sharing is disabled for your group"),
        ), patch.object(project_models, "get_public_url", return_value="https://chat.example"):
            result = project_models.create_project_link_share(db, "owner-1", "project-1")

        assert result["share_id"]
        assert project.link_share_id == result["share_id"]
        assert result["share_url"] == f"https://chat.example/projects/join/{result['share_id']}"

    def test_link_share_password_uses_chat_artifact_length_policy(self):
        project = _project()
        db = _FakeDb(project=project)

        with patch.object(project_models, "ensure_project_sharing_allowed", return_value=None), patch.object(
            project_models,
            "get_public_url",
            return_value="https://chat.example",
        ), patch.object(project_models, "hash_password", side_effect=lambda value: f"hash:{value}"):
            with pytest.raises(HTTPException) as short_error:
                project_models.create_project_link_share(
                    db,
                    "owner-1",
                    "project-1",
                    password="short",
                    password_provided=True,
                )
            assert short_error.value.status_code == 400
            assert "at least 8" in short_error.value.detail

            with pytest.raises(HTTPException) as long_error:
                project_models.create_project_link_share(
                    db,
                    "owner-1",
                    "project-1",
                    password="x" * 257,
                    password_provided=True,
                )
            assert long_error.value.status_code == 400
            assert "at most 256" in long_error.value.detail

            result = project_models.create_project_link_share(
                db,
                "owner-1",
                "project-1",
                password="valid-password",
                password_provided=True,
            )

        assert result["has_password"] is True
        assert project.link_share_password_hash == "hash:valid-password"

    def test_join_password_attempt_limiter_blocks_repeated_failures_per_share_and_ip(self):
        project = _project(link_share_password_hash="hash")
        db = MagicMock()

        with patch.object(project_models, "get_project_by_share_id", return_value=project), patch.object(
            project_models,
            "verify_password",
            return_value=False,
        ), patch.object(project_models, "get_redis_client", return_value=None):
            for _ in range(project_models.PROJECT_SHARE_PASSWORD_ATTEMPT_LIMIT):
                with pytest.raises(HTTPException) as invalid:
                    project_models.join_project_via_link(
                        db,
                        "viewer-1",
                        "share-1",
                        password="wrong-password",
                        client_ip="203.0.113.10",
                    )
                assert invalid.value.status_code == 401

            with pytest.raises(HTTPException) as blocked:
                project_models.join_project_via_link(
                    db,
                    "viewer-1",
                    "share-1",
                    password="wrong-password",
                    client_ip="203.0.113.10",
                )

        assert blocked.value.status_code == 429

    def test_valid_join_password_clears_failed_attempts(self):
        project = _project(link_share_password_hash="hash")
        member = SimpleNamespace(project_id="project-1", user_id="viewer-1")
        db = _FakeDb(member=member)

        with patch.object(project_models, "get_project_by_share_id", return_value=project), patch.object(
            project_models,
            "verify_password",
            side_effect=[False, True],
        ), patch.object(project_models, "get_redis_client", return_value=None):
            with pytest.raises(HTTPException):
                project_models.join_project_via_link(
                    db,
                    "viewer-1",
                    "share-1",
                    password="wrong-password",
                    client_ip="203.0.113.10",
                )

            result = project_models.join_project_via_link(
                db,
                "viewer-1",
                "share-1",
                password="valid-password",
                client_ip="203.0.113.10",
            )

        assert result is member
        assert project_models._PROJECT_SHARE_PASSWORD_ATTEMPTS == {}

    def test_join_project_via_link_returns_existing_member_after_integrity_error_race(self):
        project = _project(link_share_password_hash=None)
        existing_member = SimpleNamespace(project_id="project-1", user_id="viewer-1")
        db = _SequentialMemberDb(
            project=project,
            member_results=[None, existing_member],
            commit_exception=IntegrityError("duplicate key", None, None),
        )

        with patch.object(project_models, "get_project_by_share_id", return_value=project):
            result = project_models.join_project_via_link(db, "viewer-1", "share-1")

        assert result is existing_member
        assert db.commits == 1
        assert db.rollbacks == 1

    def test_add_project_member_returns_existing_member_after_integrity_error_race(self):
        project = _project()
        existing_member = SimpleNamespace(project_id="project-1", user_id="viewer-2")
        db = _SequentialMemberDb(
            project=project,
            member_results=[None, existing_member],
            commit_exception=IntegrityError("duplicate key", None, None),
        )

        result = project_models.add_project_member(db, "owner-1", "project-1", "viewer-2")

        assert result is existing_member
        assert db.commits == 1
        assert db.rollbacks == 1

    def test_get_project_by_share_id_preserves_existing_link_when_owner_sharing_disabled(self):
        project = _project()
        db = _FakeDb(project=project)

        result = project_models.get_project_by_share_id(db, "share-1")

        assert result is project

    def test_preview_preserves_existing_link_when_owner_sharing_disabled(self):
        project = _project()
        db = _FakeDb(project=project)

        with patch.object(project_models, "_get_user_display_name", return_value="Owner"):
            preview = project_models.get_project_share_preview(db, "share-1", requesting_user_id="viewer-1")

        assert preview["project_id"] == "project-1"
        assert preview["owner_name"] == "Owner"

    def test_join_preserves_existing_link_when_owner_sharing_disabled(self):
        project = _project()
        db = _FakeDb(project=project)

        member = project_models.join_project_via_link(db, "viewer-1", "share-1")

        assert member.project_id == "project-1"
        assert member.user_id == "viewer-1"

    def test_password_protected_preview_omits_settings_until_join(self):
        project = _project(
            link_share_password_hash="hash",
            settings={
                "icon": "sparkles",
                "icon_color": "#00AAFF",
                "system_instruction": "top secret prompt",
                "separate_memory_enabled": True,
            },
        )
        db = _FakeDb(project=project, member_count=3)

        with patch.object(project_models, "get_project_by_share_id", return_value=project), patch.object(
            project_models,
            "_get_user_display_name",
            return_value="Owner",
        ):
            preview = project_models.get_project_share_preview(db, "share-1", requesting_user_id="viewer-1")

        assert preview["project_id"] == "project-1"
        assert preview["title"] == "Shared project"
        assert preview["owner_name"] == "Owner"
        assert preview["member_count"] == 3
        assert preview["password_required"] is True
        assert preview["settings"] is None

    def test_unprotected_preview_returns_project_settings(self):
        project = _project(
            link_share_password_hash=None,
            settings={
                "icon": "sparkles",
                "icon_color": "#00AAFF",
                "system_instruction": "shared prompt",
                "separate_memory_enabled": True,
            },
        )
        db = _FakeDb(project=project, member_count=2)

        with patch.object(project_models, "get_project_by_share_id", return_value=project), patch.object(
            project_models,
            "_get_user_display_name",
            return_value="Owner",
        ):
            preview = project_models.get_project_share_preview(db, "share-1", requesting_user_id="viewer-1")

        assert preview["password_required"] is False
        assert preview["settings"] == {
            "icon": "sparkles",
            "icon_color": "#00AAFF",
            "system_instruction": "shared prompt",
            "separate_memory_enabled": True,
        }
