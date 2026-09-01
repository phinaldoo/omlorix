import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.users import models as user_models


class _FakeQuery:
    def __init__(self, rows=None, first_result=None):
        self._rows = rows if rows is not None else []
        self._first_result = first_result

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._first_result

    def delete(self, synchronize_session=False):
        return len(self._rows)

    def update(self, _values, synchronize_session=False):
        return len(self._rows)


class _FakeDb:
    def __init__(self, *, user, user_files=None, events=None, commit_error=None, query_results=None):
        self._user = user
        self._user_files = list(user_files or [])
        self._events = events if events is not None else []
        self._commit_error = commit_error
        self._query_results = dict(query_results or {})
        self.rollback = MagicMock()

    def query(self, model):
        model_class = getattr(model, "class_", model)
        model_name = getattr(model_class, "__name__", "")
        model_key = getattr(model, "key", None)
        if model_class is user_models.User:
            return _FakeQuery(first_result=self._user)
        if model_name == "Files":
            return _FakeQuery(rows=self._user_files)
        if model_key is not None:
            return _FakeQuery(rows=self._query_results.get((model_name, model_key), []))
        if model_name:
            return _FakeQuery(rows=self._query_results.get(model_name, []))
        return _FakeQuery()

    def execute(self, _statement):
        return SimpleNamespace(rowcount=0)

    def delete(self, obj):
        self._events.append(("db_delete", getattr(obj, "id", None)))

    def flush(self):
        """Mirror the SQLAlchemy Session flush method used during cleanup."""

    def commit(self):
        self._events.append(("commit", None))
        if self._commit_error is not None:
            raise self._commit_error


class HardDeleteUserStorageCleanupTests:
    def test_rejected_hard_delete_does_not_publish_erasure_intent(self):
        events = []
        user = SimpleNamespace(id="user-1", role="user")
        db = _FakeDb(user=user, events=events)
        retained_policy = {
            "mode": "retain",
            "retention_days": None,
            "delete_immediately": False,
        }

        with patch(
            "app.users.deletion_policy.get_auth_log_user_deletion_retention_policy",
            return_value=retained_policy,
        ), patch(
            "app.users.deletion_policy.get_audit_log_user_deletion_retention_policy",
            return_value=retained_policy,
        ), patch(
            "app.groups.models.ensure_user_can_become_ineligible_manager",
            side_effect=ValueError("replacement manager required"),
        ), patch(
            "app.users.erasure_ledger.record_user_erasure_intent"
        ) as record_intent:
            with pytest.raises(ValueError, match="replacement manager required"):
                user_models.hard_delete_user(
                    db,
                    "user-1",
                    notify_user=False,
                )

        record_intent.assert_not_called()
        assert ("commit", None) not in events

    def test_immediate_hard_delete_fences_audit_events_before_account_commit(
        self,
        tmp_path,
    ):
        events = []
        user = SimpleNamespace(id="user-1", role="user")
        db = _FakeDb(user=user, events=events)
        retained_policy = {
            "mode": "retain",
            "retention_days": None,
            "delete_immediately": False,
        }
        immediate_policy = {
            "mode": "delete_instantly",
            "retention_days": None,
            "delete_immediately": True,
        }

        with patch("app.auth.session_store.revoke_user_sessions"), patch(
            "app.files.storage.get_local_user_files_base_dir", return_value=tmp_path
        ), patch("app.agents.models.delete_user_linked_agents"), patch(
            "app.userNotifications.models.remove_user_references_from_notifications"
        ), patch("app.skills.models._delete_skill_directory"), patch(
            "app.users.deletion_policy.get_auth_log_user_deletion_retention_policy",
            return_value=retained_policy,
        ), patch(
            "app.users.deletion_policy.get_audit_log_user_deletion_retention_policy",
            return_value=immediate_policy,
        ), patch(
            "app.users.erasure_ledger.record_user_erasure_intent",
            return_value="erasure-operation-1",
        ), patch(
            "app.users.erasure_ledger.record_completed_user_erasure"
        ), patch(
            "app.workers.events.enqueue_audit_erasure",
            side_effect=lambda _db, **kwargs: events.append(
                ("audit_cleanup_enqueued", kwargs["user_id"], kwargs["commit"])
            ),
        ), patch(
            "app.workers.models.erase_user_audit_event_state",
            side_effect=lambda _db, *, user_id, commit: events.append(
                ("audit_fenced", user_id, commit)
            ),
        ), patch("shutil.rmtree"):
            result = user_models.hard_delete_user(
                db,
                "user-1",
                notify_user=False,
            )

        assert result is True
        assert ("audit_fenced", "user-1", False) in events
        assert ("audit_cleanup_enqueued", "user-1", False) in events
        assert events.index(("audit_fenced", "user-1", False)) < events.index(
            ("commit", None)
        )
        assert events.index(("audit_cleanup_enqueued", "user-1", False)) < events.index(
            ("commit", None)
        )

    def test_hard_delete_user_cleans_chatless_deep_research_after_commit(self, tmp_path):
        """User cleanup must not depend on a research run having a chat ID."""

        events = []
        user = SimpleNamespace(id="user-1", role="user")
        chatless_run = SimpleNamespace(
            id="run-1",
            user_id="user-1",
            chat_id=None,
        )
        descriptor = {
            "user_id": "user-1",
            "run_id": "run-1",
            "storage_provider": "s3",
            "relative_paths": ["final-report.md"],
        }
        db = _FakeDb(
            user=user,
            events=events,
            query_results={"DeepResearchRun": [chatless_run]},
        )
        retained_policy = {
            "mode": "retain",
            "retention_days": None,
            "delete_immediately": False,
        }

        with patch("app.auth.session_store.revoke_user_sessions"), patch(
            "app.files.storage.get_local_user_files_base_dir", return_value=tmp_path
        ), patch("app.agents.models.delete_user_linked_agents"), patch(
            "app.userNotifications.models.remove_user_references_from_notifications"
        ), patch("app.skills.models._delete_skill_directory"), patch(
            "app.tools.deep_research.storage.deep_research_run_cleanup_descriptor",
            return_value=descriptor,
        ), patch(
            "app.tools.deep_research.storage.delete_deep_research_run_artifacts",
            side_effect=lambda **kwargs: events.append(
                ("delete_deep_research", kwargs["run_id"])
            ),
        ), patch(
            "app.users.deletion_policy.get_auth_log_user_deletion_retention_policy",
            return_value=retained_policy,
        ), patch(
            "app.users.deletion_policy.get_audit_log_user_deletion_retention_policy",
            return_value=retained_policy,
        ), patch(
            "app.users.erasure_ledger.record_user_erasure_intent",
            side_effect=lambda *args, **kwargs: events.append(
                ("ledger_intent", args[0])
            ) or "erasure-operation-1",
        ), patch(
            "app.users.erasure_ledger.record_completed_user_erasure",
            side_effect=lambda *args, **kwargs: events.append(("ledger_record", args[0])),
        ), patch("shutil.rmtree"):
            result = user_models.hard_delete_user(
                db,
                "user-1",
                notify_user=False,
            )

        assert result is True
        assert ("delete_deep_research", "run-1") in events
        assert events.index(("ledger_intent", "user-1")) < events.index(("commit", None))
        assert events.index(("commit", None)) < events.index(
            ("ledger_record", "user-1")
        )
        assert events.index(("ledger_record", "user-1")) < events.index(
            ("delete_deep_research", "run-1")
        )

    def test_hard_delete_user_deletes_storage_only_after_commit(self, tmp_path):
        events = []
        user = SimpleNamespace(id="user-1", role="user")
        file_row = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="avatar.png",
            storage_provider="s3",
            storage_key="user-1/avatar.png",
        )
        adapter = SimpleNamespace(delete_file=lambda storage_key: events.append(("delete_file", storage_key)))
        db = _FakeDb(user=user, user_files=[file_row], events=events)

        with patch("app.auth.session_store.revoke_user_sessions"), patch(
            "app.files.storage.get_local_user_files_base_dir", return_value=tmp_path
        ), patch("app.files.storage.get_user_file_storage_adapter_for_provider", return_value=adapter), patch(
            "app.agents.models.delete_user_linked_agents"
        ), patch("app.userNotifications.models.remove_user_references_from_notifications"), patch(
            "app.skills.models._delete_skill_directory"
        ), patch("shutil.rmtree") as mock_rmtree:
            result = user_models.hard_delete_user(
                db,
                "user-1",
                record_erasure=False,
                notify_user=False,
            )

        assert result is True
        assert ("delete_file", "user-1/avatar.png") in events
        assert events.index(("commit", None)) < events.index(("delete_file", "user-1/avatar.png"))
        mock_rmtree.assert_called_once_with(tmp_path / "user-1", ignore_errors=True)

    def test_hard_delete_user_does_not_delete_storage_when_commit_fails(self, tmp_path):
        events = []
        user = SimpleNamespace(id="user-1", role="user")
        file_row = SimpleNamespace(
            id="file-1",
            user_id="user-1",
            file_name="avatar.png",
            storage_provider="s3",
            storage_key="user-1/avatar.png",
        )
        adapter = SimpleNamespace(delete_file=MagicMock())
        db = _FakeDb(user=user, user_files=[file_row], events=events, commit_error=RuntimeError("commit failed"))

        with patch("app.auth.session_store.revoke_user_sessions"), patch(
            "app.files.storage.get_local_user_files_base_dir", return_value=tmp_path
        ), patch("app.files.storage.get_user_file_storage_adapter_for_provider", return_value=adapter), patch(
            "app.agents.models.delete_user_linked_agents"
        ), patch("app.userNotifications.models.remove_user_references_from_notifications"), patch(
            "app.skills.models._delete_skill_directory"
        ), patch("shutil.rmtree") as mock_rmtree:
            with pytest.raises(RuntimeError, match="commit failed"):
                user_models.hard_delete_user(
                    db,
                    "user-1",
                    record_erasure=False,
                    notify_user=False,
                )

        db.rollback.assert_called_once()
        adapter.delete_file.assert_not_called()
        mock_rmtree.assert_not_called()

    def test_hard_delete_user_defers_other_storage_cleanup_until_after_commit(self, tmp_path):
        events = []
        user = SimpleNamespace(id="user-1", role="user")
        skill_ids = [("skill-1",)]
        agent = SimpleNamespace(id="agent-1")
        asset = SimpleNamespace(
            id="asset-1",
            agent_id="agent-1",
            owner_user_id="user-1",
            file_name="agent.png",
            storage_provider="local",
            storage_key="user-1/agent.png",
        )
        presentation = SimpleNamespace(
            id="presentation-1",
            user_id="user-1",
            storage_provider="local",
            storage_prefix="user-1/presentations/presentation-1",
            slide_count=2,
        )
        db = _FakeDb(
            user=user,
            events=events,
            query_results={
                ("Skills", "id"): skill_ids,
                ("UserAgent", "id"): [(agent.id,)],
                "UserAgent": [agent],
                "UserAgentAsset": [asset],
                "SlidePresentations": [presentation],
            },
        )

        with patch("app.auth.session_store.revoke_user_sessions"), patch(
            "app.files.storage.get_local_user_files_base_dir", return_value=tmp_path
        ), patch(
            "app.agents.models.delete_storage_reference",
            side_effect=lambda **kwargs: events.append(("delete_agent_asset", kwargs["storage_key"])),
        ), patch(
            "app.skills.models._delete_skill_directory",
            side_effect=lambda _user_id, skill_id: events.append(("delete_skill_dir", skill_id)),
        ), patch(
            "app.tools.slide_presentation.storage.delete_slide_presentation_artifacts",
            side_effect=lambda **kwargs: events.append(("delete_presentation", kwargs["storage_prefix"])),
        ), patch("app.userNotifications.models.remove_user_references_from_notifications"), patch(
            "shutil.rmtree"
        ):
            result = user_models.hard_delete_user(
                db,
                "user-1",
                record_erasure=False,
                notify_user=False,
            )

        assert result is True
        assert events.index(("commit", None)) < events.index(("delete_skill_dir", "skill-1"))
        assert events.index(("commit", None)) < events.index(("delete_agent_asset", "user-1/agent.png"))
        assert events.index(("commit", None)) < events.index(
            ("delete_presentation", "user-1/presentations/presentation-1")
        )

    def test_hard_delete_user_does_not_run_other_storage_cleanup_when_commit_fails(self, tmp_path):
        user = SimpleNamespace(id="user-1", role="user")
        delete_agent_asset = MagicMock()
        delete_skill_dir = MagicMock()
        delete_presentation = MagicMock()
        db = _FakeDb(
            user=user,
            commit_error=RuntimeError("commit failed"),
            query_results={
                ("Skills", "id"): [("skill-1",)],
                ("UserAgent", "id"): [("agent-1",)],
                "UserAgent": [SimpleNamespace(id="agent-1")],
                "UserAgentAsset": [
                    SimpleNamespace(
                        id="asset-1",
                        agent_id="agent-1",
                        owner_user_id="user-1",
                        file_name="agent.png",
                        storage_provider="local",
                        storage_key="user-1/agent.png",
                    )
                ],
                "SlidePresentations": [
                    SimpleNamespace(
                        id="presentation-1",
                        user_id="user-1",
                        storage_provider="local",
                        storage_prefix="user-1/presentations/presentation-1",
                        slide_count=2,
                    )
                ],
            },
        )

        with patch("app.auth.session_store.revoke_user_sessions"), patch(
            "app.files.storage.get_local_user_files_base_dir", return_value=tmp_path
        ), patch("app.agents.models.delete_storage_reference", delete_agent_asset), patch(
            "app.skills.models._delete_skill_directory", delete_skill_dir
        ), patch(
            "app.tools.slide_presentation.storage.delete_slide_presentation_artifacts", delete_presentation
        ), patch("app.userNotifications.models.remove_user_references_from_notifications"), patch(
            "shutil.rmtree"
        ):
            with pytest.raises(RuntimeError, match="commit failed"):
                user_models.hard_delete_user(
                    db,
                    "user-1",
                    record_erasure=False,
                    notify_user=False,
                )

        db.rollback.assert_called_once()
        delete_agent_asset.assert_not_called()
        delete_skill_dir.assert_not_called()
        delete_presentation.assert_not_called()
