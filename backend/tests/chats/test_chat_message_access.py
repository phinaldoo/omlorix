import sys
import json
import inspect
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard


from app.chats import utils as chat_utils
from app.chats import router as chat_router
from app.chats import models as chat_models
from app.chats.models import ChatMessages, Chats, can_send_messages_to_chat
from app.projects.models import can_send_message_in_chat, ensure_project_access_for_chat_send
from app.utils import background as background_utils


def test_regeneration_finalizes_rate_limit_before_publishing_done():
    """The next queued turn must see the prior regeneration as released."""

    source = inspect.getsource(chat_utils.regenerate_message)
    completion_block_start = source.index("if pending_done_line is not None:")
    completion_block_end = source.index("\n    except Exception", completion_block_start)
    completion_block = source[completion_block_start:completion_block_end]

    record_index = completion_block.index("_record_completion_before_stream_publish")
    finalize_index = completion_block.index("_finalize_regeneration_rate_limit_admission")
    publish_index = completion_block.index("stream_hub.publish_line")

    assert record_index < finalize_index < publish_index


class _FakeQuery:
    def __init__(self, result=None, rows=None):
        self._result = result
        self._rows = rows or []

    def join(self, *args, **kwargs):
        """Mirror the chained SQLAlchemy query API used by bookmark lookups."""
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, chat=None, messages=None, chat_results=None):
        self.chat = chat
        self.messages = messages or []
        self.chat_results = list(chat_results) if chat_results is not None else None

    def query(self, model):
        if model is Chats:
            if self.chat_results is not None:
                result = self.chat_results.pop(0) if self.chat_results else None
                return _FakeQuery(result=result)
            return _FakeQuery(result=self.chat)
        if model is ChatMessages:
            return _FakeQuery(rows=self.messages)
        return _FakeQuery()


class _MessageDeleteQuery:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows


class _MessageDeleteDb:
    def __init__(self, *, message, chat, messages):
        self.message = message
        self.chat = chat
        self.messages = messages
        self.commits = 0
        self.deleted = []

    def query(self, model):
        if model is ChatMessages:
            return _MessageDeleteQuery(row=self.message, rows=self.messages)
        if model is Chats:
            return _MessageDeleteQuery(row=self.chat)
        return _MessageDeleteQuery()

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        return None

    def delete(self, obj):
        self.deleted.append(obj)


class _FakeTitleSession:
    def __init__(self, model, chat):
        self.model = model
        self.chat = chat

    def query(self, model):
        if model is chat_utils.Models:
            return _FakeQuery(result=self.model)
        if model is Chats:
            return _FakeQuery(result=self.chat)
        return _FakeQuery()

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _MutationQuery:
    def __init__(self, row=None):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _MutationDb:
    def __init__(self, message, chat):
        self.message = message
        self.chat = chat
        self.commits = 0
        self.deleted = []

    def query(self, model):
        if model is ChatMessages:
            return _MutationQuery(row=self.message)
        if model is Chats:
            return _MutationQuery(row=self.chat)
        return _MutationQuery()

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        return None

    def delete(self, obj):
        self.deleted.append(obj)


def _chat(meta):
    return Chats(id="chat-1", user_id="user-1", meta=meta)


def _project_message(*, role: str = "user", content: str = "before"):
    return ChatMessages(
        id="message-1",
        chat_id="chat-1",
        model_id="model-1",
        role=role,
        content=content,
    )


def _patch_send_message_basics(
    monkeypatch,
    *,
    save_temp: bool = False,
    recorded_calls: dict | None = None,
):
    chat = SimpleNamespace(id="chat-1", meta={}, project_id=None, title=None)
    model = SimpleNamespace(
        id="model-1",
        provider="openai_chat_completions",
        provider_id="provider-1",
        settings={},
        model_name="gpt-4.1-mini",
    )
    resolved_selection = SimpleNamespace(
        selected_model_id=model.id,
        base_model=model,
        model_kind="base",
        agent=None,
        agent_instruction=None,
        agent_skill_ids=[],
        asset_descriptors_by_category={},
    )
    start_calls = []
    recorded_calls = recorded_calls if recorded_calls is not None else {}
    create_chat_calls = recorded_calls.setdefault("create_chat", [])
    mapping_calls = recorded_calls.setdefault("set_active", [])
    ownership_calls = recorded_calls.setdefault("ownership", [])
    generation_owners = recorded_calls.setdefault("generation_owners", {})

    def _create_chat(*args, **kwargs):
        create_chat_calls.append((args, kwargs))
        return chat

    def _reserve_generation(generation_id, user_id):
        ownership_calls.append(("reserve", generation_id, user_id))
        if generation_id in generation_owners:
            return False
        generation_owners[generation_id] = user_id
        return True

    def _generation_is_owned_by(generation_id, user_id):
        ownership_calls.append(("is_owned_by", generation_id, user_id))
        return generation_owners.get(generation_id) == user_id

    monkeypatch.setattr(chat_utils, "resolve_selected_model_for_user", lambda *args, **kwargs: resolved_selection)
    monkeypatch.setattr(chat_utils, "ensure_user_access_to_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_utils, "_admit_rate_limited_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_utils, "get_user_group_setting_value", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        chat_utils,
        "get_group_setting_value",
        lambda *_args, **kwargs: (
            True
            if _args[1:3] == ("chat", "allow_temporary_chat")
            else save_temp
            if _args[1:3] == ("chat", "save_temp_chats")
            else False
        ),
    )
    monkeypatch.setattr(
        chat_utils,
        "_collect_skill_file_attachment_ids",
        lambda *args, **kwargs: {"images": [], "videos": [], "audios": [], "documents": []},
    )
    monkeypatch.setattr(chat_utils, "resolve_chat_reference_payload", lambda *args, **kwargs: ([], None))
    monkeypatch.setattr(chat_utils, "create_chat", _create_chat)
    monkeypatch.setattr(chat_utils, "_cleanup_chat_after_empty_transcript", MagicMock())
    monkeypatch.setattr(chat_utils, "finalize_rate_limit_admission", MagicMock())
    monkeypatch.setattr(
        chat_utils,
        "stream_hub",
        SimpleNamespace(
            start=lambda *args, **kwargs: start_calls.append((args, kwargs)),
            publish_line=lambda *args, **kwargs: None,
            mark_done=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(
        chat_utils,
        "cancel_registry",
        SimpleNamespace(
            reserve=_reserve_generation,
            is_owned_by=_generation_is_owned_by,
            set_active=lambda *args, **kwargs: mapping_calls.append((args, kwargs)),
            clear=lambda generation_id: generation_owners.pop(generation_id, None),
        ),
    )

    return chat, start_calls


def test_delete_chat_message_cancels_active_generation_without_deleting_until_retry(monkeypatch):
    message = _project_message(role="user")
    assistant_message = ChatMessages(
        id="message-2",
        chat_id="chat-1",
        model_id="model-1",
        role="assistant",
        content="reply",
    )
    chat = Chats(id="chat-1", user_id="user-1", project_id=None, meta={})
    db = _MessageDeleteDb(message=message, chat=chat, messages=[message, assistant_message])
    cancelled_generation_ids = []

    monkeypatch.setattr("app.groups.init.get_user_group_setting_value", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(chat_models.stream_hub, "get_status", lambda _chat_id: {"active": True, "generation_id": "gen-1"})
    monkeypatch.setattr(chat_models.cancel_registry, "cancel", cancelled_generation_ids.append)

    with pytest.raises(HTTPException) as exc_info:
        chat_models.delete_chat_message("user-1", "group-1", "message-1", db)

    assert exc_info.value.status_code == 409
    assert cancelled_generation_ids == ["gen-1"]
    assert db.deleted == []
    assert db.commits == 0


@pytest.mark.parametrize(
    "meta",
    [
        {"shadow_deleted": True},
        {"status": "temp"},
        '{"shadow_deleted": true}',
        '{"status": "temp"}',
    ],
)
def test_get_chat_messages_rejects_deleted_and_temporary_chats(meta):
    with pytest.raises(HTTPException) as exc_info:
        chat_utils.get_chat_messages("user-1", "chat-1", _FakeDb(chat=_chat(meta)))

    assert exc_info.value.status_code == 404


def test_get_chat_messages_hides_bookmarks_for_project_collaborators(monkeypatch):
    owner_chat = _chat({})
    owner_chat.project_id = "project-1"
    owner_chat.user_id = "owner-1"
    message = _project_message()
    message.bookmarked = True

    monkeypatch.setattr("app.projects.models.has_project_access", lambda *args, **kwargs: True)

    messages = chat_utils.get_chat_messages(
        "collaborator-1",
        "chat-1",
        _FakeDb(chat_results=[None, owner_chat], messages=[message]),
    )

    assert messages[0]["bookmarked"] is False


def test_get_chat_messages_includes_bookmarks_for_chat_owner():
    owner_chat = _chat({})
    message = _project_message()
    message.bookmarked = True

    messages = chat_utils.get_chat_messages("user-1", "chat-1", _FakeDb(chat=owner_chat, messages=[message]))

    assert messages[0]["bookmarked"] is True


def test_archived_chat_messages_remain_readable_for_bookmark_navigation():
    owner_chat = _chat({})
    owner_chat.archived = True
    message = _project_message()
    message.bookmarked = True

    messages = chat_utils.get_chat_messages("user-1", "chat-1", _FakeDb(chat=owner_chat, messages=[message]))

    assert messages[0]["id"] == message.id
    assert messages[0]["bookmarked"] is True


def test_get_bookmarked_messages_omits_model_id_from_workspace_payload():
    """Bookmark cards should not receive assistant model metadata anymore."""
    owner_chat = _chat({})
    owner_chat.title = "Saved Chat"
    message = _project_message(role="assistant", content="reply")
    message.bookmarked = True

    bookmarks = chat_models.get_bookmarked_messages(
        "user-1",
        _FakeDb(chat_results=[owner_chat], messages=[message]),
    )

    assert len(bookmarks) == 1
    assert "model_id" not in bookmarks[0]


def test_get_bookmarked_messages_keeps_archived_source_chats_available():
    owner_chat = _chat({})
    owner_chat.title = "Archived Saved Chat"
    owner_chat.archived = True
    message = _project_message(role="user", content="saved prompt")
    message.bookmarked = True

    bookmarks = chat_models.get_bookmarked_messages(
        "user-1",
        _FakeDb(chat_results=[owner_chat], messages=[message]),
    )

    assert len(bookmarks) == 1
    assert bookmarks[0]["chat_id"] == owner_chat.id
    assert bookmarks[0]["id"] == message.id


def test_shared_chat_resolution_uses_same_deleted_temporary_guard(monkeypatch):
    chat = _chat({"shadow_deleted": True})
    chat.share_id = "share-1"
    chat.share = {}

    monkeypatch.setattr(chat_utils, "ensure_chat_sharing_enabled_for_user", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        chat_utils._resolve_shared_chat_or_404(_FakeDb(chat=chat), "share-1")

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("chat_user_id", "meta"),
    [
        ("user-2", {}),
        ("user-1", {"shadow_deleted": True}),
        ("user-1", {"status": "temp"}),
        ("user-1", '{"shadow_deleted": true}'),
        ("user-1", '{"status": "temp"}'),
    ],
)
def test_can_send_message_rejects_unowned_deleted_and_temporary_chats(chat_user_id, meta):
    chat = Chats(id="chat-1", user_id=chat_user_id, meta=meta)

    assert can_send_message_in_chat(_FakeDb(chat=chat), "user-1", "chat-1") is False


def test_can_send_message_rejects_archived_chats():
    chat = Chats(id="chat-1", user_id="user-1", meta={}, archived=True)

    assert can_send_message_in_chat(_FakeDb(chat=chat), "user-1", "chat-1") is False
    assert can_send_messages_to_chat(chat) is False


def test_send_route_rejects_unavailable_chat_before_background_job():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    db = _FakeDb(chat=Chats(id="chat-1", user_id="user-2", meta={}))

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ), patch.object(chat_router.background_task_executor, "submit") as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(model_id="model-1", message="hello", chat_id="chat-1"),
                request=request,
                custom_settings={},
                db=db,
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 404
    submit.assert_not_called()


def test_send_route_rejects_archived_chat_before_background_job():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    db = _FakeDb(chat=Chats(id="chat-1", user_id="user-1", meta={}, archived=True))

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ), patch.object(chat_router.background_task_executor, "submit") as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(model_id="model-1", message="hello", chat_id="chat-1"),
                request=request,
                custom_settings={},
                db=db,
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 404
    submit.assert_not_called()


def test_send_route_rejects_missing_model_before_background_job():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ) as log_chat_event, patch.object(chat_router.background_task_executor, "submit") as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(model_id="  ", message="hello"),
                request=request,
                custom_settings={},
                db=_FakeDb(),
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"code": "chat_model_required"}
    assert log_chat_event.call_args.args[4] == {"chat_id": None, "reason": "model_required"}
    submit.assert_not_called()


def test_send_route_rejects_unknown_model_before_streaming_response():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "resolve_chat_model_for_user",
        side_effect=HTTPException(status_code=404, detail="Agent not found"),
    ), patch.object(
        chat_router,
        "_log_chat_event",
    ) as log_chat_event, patch.object(chat_router.background_task_executor, "submit") as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(model_id="unknown-model", message="hello"),
                request=request,
                custom_settings={},
                db=_FakeDb(),
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Agent not found"
    assert log_chat_event.call_args.args[4] == {
        "chat_id": None,
        "reason": "model_unavailable",
        "status_code": 404,
    }
    submit.assert_not_called()


def test_send_route_rejects_provider_down_before_streaming():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    detail = "This model is currently unavailable because its provider is down"
    resolved_selection = SimpleNamespace(
        selected_model_id="base-model-1",
        base_model=SimpleNamespace(id="base-model-1"),
    )
    db = _FakeDb()

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_utils,
        "resolve_selected_model_for_user",
        return_value=resolved_selection,
    ), patch.object(
        chat_utils,
        "ensure_user_access_to_model",
        side_effect=HTTPException(status_code=503, detail=detail),
    ) as ensure_access, patch.object(
        chat_router,
        "_log_chat_event",
    ) as log_chat_event, patch.object(chat_router.background_task_executor, "submit") as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(model_id="selected-model-1", message="hello"),
                request=request,
                custom_settings={},
                db=db,
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == detail
    ensure_access.assert_called_once_with("user-1", "base-model-1", db)
    assert log_chat_event.call_args.args[4] == {
        "chat_id": None,
        "reason": "model_unavailable",
        "status_code": 503,
    }
    submit.assert_not_called()


def test_chat_model_resolution_checks_agent_policy_and_backing_provider():
    db = _FakeDb()
    resolved_selection = SimpleNamespace(
        selected_model_id="agent-1",
        base_model=SimpleNamespace(id="base-model-1"),
    )

    with patch.object(
        chat_utils,
        "resolve_selected_model_for_user",
        return_value=resolved_selection,
    ), patch.object(chat_utils, "ensure_user_access_to_model") as ensure_access:
        result = chat_utils.resolve_chat_model_for_user(
            db,
            user_id="user-1",
            model_id="agent-1",
        )

    assert result is resolved_selection
    assert [entry.args for entry in ensure_access.call_args_list] == [
        ("user-1", "agent-1", db),
        ("user-1", "base-model-1", db),
    ]


def test_send_route_rejects_inaccessible_project_before_background_job():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ) as log_chat_event, patch("app.projects.models.has_project_access", return_value=False), patch.object(
        chat_router.background_task_executor,
        "submit",
    ) as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(model_id="model-1", message="hello", project_id="project-1"),
                request=request,
                custom_settings={},
                db=_FakeDb(),
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 404
    assert log_chat_event.call_args.args[4]["project_id"] == "project-1"
    submit.assert_not_called()


def test_send_route_audits_persisted_project_when_request_scope_is_ignored():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    db = _FakeDb(chat=Chats(id="chat-1", user_id="user-1", project_id="project-1", meta={}))

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ) as log_chat_event, patch("app.projects.models.has_project_access", return_value=False), patch.object(
        chat_router.background_task_executor,
        "submit",
    ) as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(
                    model_id="model-1",
                    message="hello",
                    chat_id="chat-1",
                    project_id="client-controlled-project",
                ),
                request=request,
                custom_settings={},
                db=db,
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 404
    assert log_chat_event.call_args.args[4] == {
        "chat_id": "chat-1",
        "project_id": "project-1",
        "reason": "project_unavailable",
    }
    submit.assert_not_called()


def test_send_route_audits_request_project_when_existing_chat_has_no_project():
    """Use the normalized request scope when the persisted chat has no project."""

    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})
    db = _FakeDb(chat=Chats(id="chat-1", user_id="user-1", project_id=None, meta={}))

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ) as log_chat_event, patch.object(
        chat_router,
        "ensure_project_access_for_chat_send",
        side_effect=HTTPException(status_code=404, detail="Project not found"),
    ), patch.object(
        chat_router.background_task_executor,
        "submit",
    ) as submit:
        with pytest.raises(HTTPException) as exc_info:
            chat_router.send(
                payload=chat_router.SendChatRequest(
                    model_id="model-1",
                    message="hello",
                    chat_id="chat-1",
                    project_id="  requested-project  ",
                ),
                request=request,
                custom_settings={},
                db=db,
                db_log=MagicMock(),
                user=user,
                byok=None,
            )

    assert exc_info.value.status_code == 404
    assert log_chat_event.call_args.args[4] == {
        "chat_id": "chat-1",
        "project_id": "requested-project",
        "reason": "project_unavailable",
    }
    submit.assert_not_called()


def test_edit_chat_message_rejects_project_chat_without_current_access(monkeypatch):
    message = _project_message()
    chat = Chats(id="chat-1", user_id="user-1", project_id="project-1", meta={})
    db = _MutationDb(message=message, chat=chat)

    monkeypatch.setattr("app.projects.models.has_project_access", lambda *_args, **_kwargs: False)

    with pytest.raises(HTTPException) as exc_info:
        chat_models.edit_chat_message("user-1", "message-1", "after", db)

    assert exc_info.value.status_code == 404
    assert message.content == "before"
    assert db.commits == 0


def test_edit_chat_message_updates_chat_references(monkeypatch):
    message = _project_message()
    chat = Chats(id="chat-1", user_id="user-1", project_id=None, meta={})
    db = _MutationDb(message=message, chat=chat)
    resolved_references = [
        {
            "chat_id": "ref-1",
            "title": "Reference chat",
            "last_updated_at": None,
            "snippet": "Useful context",
            "message_count": 2,
            "estimated_chars": 120,
        }
    ]

    def fake_resolve(user_id, db_arg, chat_reference_ids, **kwargs):
        assert user_id == "user-1"
        assert db_arg is db
        assert chat_reference_ids == ["ref-1"]
        assert kwargs["current_chat_id"] == "chat-1"
        return resolved_references, "context"

    monkeypatch.setattr(chat_utils, "resolve_chat_reference_payload", fake_resolve)

    chat_models.edit_chat_message("user-1", "message-1", "after", db, chat_reference_ids=["ref-1"])

    decoded = json.loads(message.content)
    assert decoded[0]["content"] == "after"
    assert decoded[0]["chat_references"] == resolved_references
    assert db.commits == 1


def test_delete_chat_message_rejects_project_chat_without_current_access(monkeypatch):
    message = _project_message()
    chat = Chats(id="chat-1", user_id="user-1", project_id="project-1", meta={})
    db = _MutationDb(message=message, chat=chat)

    monkeypatch.setattr("app.groups.init.get_user_group_setting_value", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.projects.models.has_project_access", lambda *_args, **_kwargs: False)

    with pytest.raises(HTTPException) as exc_info:
        chat_models.delete_chat_message("user-1", "group-1", "message-1", db)

    assert exc_info.value.status_code == 404
    assert db.deleted == []
    assert db.commits == 0


@pytest.mark.parametrize(
    ("meta", "archived"),
    [
        ({"shadow_deleted": True}, False),
        ({"status": "temp"}, False),
        ({}, True),
    ],
)
def test_send_message_rejects_unavailable_chat_before_persistence(meta, archived):
    stream = chat_utils.send_message(
        "user-1",
        "group-1",
        "chat-1",
        "hello",
        None,
        None,
        None,
        None,
        None,
        None,
        "model-1",
        None,
        {},
        _FakeDb(chat=Chats(id="chat-1", user_id="user-1", meta=meta, archived=archived)),
    )

    with pytest.raises(HTTPException) as exc_info:
        next(stream)

    assert exc_info.value.status_code == 404


def test_send_message_uses_dedicated_executor_for_title_generation(monkeypatch):
    published_lines = []
    lifecycle_events = []
    title_submit = MagicMock()
    chat = SimpleNamespace(id="chat-1", meta={}, project_id=None, title=None)
    model = SimpleNamespace(
        id="model-1",
        provider="openai_chat_completions",
        provider_id="provider-1",
        settings={},
        model_name="gpt-4.1-mini",
    )
    resolved_selection = SimpleNamespace(
        selected_model_id=model.id,
        base_model=model,
        model_kind="base",
        agent=None,
        agent_instruction=None,
        agent_skill_ids=[],
        asset_descriptors_by_category={},
    )
    db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None)

    monkeypatch.setattr(chat_utils, "resolve_selected_model_for_user", lambda *args, **kwargs: resolved_selection)
    monkeypatch.setattr(chat_utils, "ensure_user_access_to_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_utils, "_admit_rate_limited_chat_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_utils, "get_user_group_setting_value", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_utils, "get_group_setting_value", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_utils, "_collect_skill_file_attachment_ids", lambda *args, **kwargs: {"images": [], "videos": [], "audios": [], "documents": []})
    monkeypatch.setattr(chat_utils, "resolve_chat_reference_payload", lambda *args, **kwargs: ([], None))
    monkeypatch.setattr(chat_utils, "create_chat", lambda *args, **kwargs: chat)
    monkeypatch.setattr(chat_utils, "create_chat_message", lambda *args, **kwargs: SimpleNamespace(id="msg-1"))
    monkeypatch.setattr(chat_utils, "db_get_chat_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat_utils, "_filter_latest_assistant_versions", lambda history: history)
    monkeypatch.setattr(chat_utils, "_compose_skill_content", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat_utils, "_compose_prompt_content", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat_utils, "get_user_personality_system_instruction_section", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat_utils, "_build_system_instruction_sections", lambda **kwargs: [])
    monkeypatch.setattr(chat_utils, "_assert_generation_provider_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_utils, "get_title_generation_prompt", lambda *args, **kwargs: "Prompt")
    monkeypatch.setattr(
        chat_utils,
        "finalize_rate_limit_admission",
        lambda *args, **kwargs: lifecycle_events.append("admission_finalized"),
    )
    monkeypatch.setattr(chat_utils, "SessionLocal", lambda: _FakeTitleSession(model=model, chat=chat))
    monkeypatch.setattr(
        chat_utils,
        "stream_hub",
        SimpleNamespace(
            start=lambda *args, **kwargs: None,
            publish_line=lambda _generation_id, line: published_lines.append(line),
            mark_done=lambda *args, **kwargs: lifecycle_events.append("done_published"),
        ),
    )
    monkeypatch.setattr(
        chat_utils,
        "cancel_registry",
        SimpleNamespace(
            set_active=lambda *args, **kwargs: None,
            clear=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(
        chat_utils,
        "openai_chat_completions_chat",
        lambda *args, **kwargs: iter(['{"t":"d"}\n']),
    )
    monkeypatch.setattr(
        background_utils.background_task_executor,
        "submit",
        MagicMock(side_effect=AssertionError("shared executor should not be used for title generation")),
    )

    def _run_title_task(fn, *args, **kwargs):
        title_submit(fn, *args, **kwargs)
        fn(*args, **kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(chat_utils.title_generation_executor, "submit", _run_title_task)

    stream = chat_utils.send_message(
        "user-1",
        "group-1",
        None,
        "hello world",
        None,
        None,
        None,
        None,
        None,
        None,
        "model-1",
        None,
        {},
        db,
    )

    lines = []
    for line in stream:
        parsed_line = json.loads(line)
        if parsed_line.get("t") == "d":
            lifecycle_events.append("done_observed")
        lines.append(line)

    assert title_submit.call_count == 1
    assert any('"t": "n_t"' in line for line in lines)
    assert any('"t": "n_t"' in line for line in published_lines)
    assert lifecycle_events.count("admission_finalized") == 1
    assert lifecycle_events.count("done_observed") == 1
    assert lifecycle_events.count("done_published") == 1
    assert lifecycle_events.index("admission_finalized") < lifecycle_events.index("done_published")


def test_send_message_rejects_inaccessible_requested_project_before_persistence():
    with patch("app.projects.models.has_project_access", return_value=False):
        stream = chat_utils.send_message(
            "user-1",
            "group-1",
            None,
            "hello",
            None,
            None,
            None,
            None,
            "project-1",
            None,
            "model-1",
            None,
            {},
            _FakeDb(),
        )

        with pytest.raises(HTTPException) as exc_info:
            next(stream)

    assert exc_info.value.status_code == 404


def test_chat_project_scope_is_server_owned_after_creation():
    """Persisted scope wins, while a new chat can still use its requested project."""
    scoped_chat = Chats(id="chat-1", user_id="user-1", project_id="project-stored", meta={})
    db = _FakeDb(chat=scoped_chat)

    with patch("app.projects.models.has_project_access", return_value=True) as has_access:
        saved_scope = ensure_project_access_for_chat_send(
            db,
            "user-1",
            project_id="project-requested",
            chat=scoped_chat,
        )
        new_scope = ensure_project_access_for_chat_send(
            db,
            "user-1",
            project_id="project-requested",
            chat=None,
        )

    assert saved_scope == ("project-stored", "project-stored")
    assert new_scope == ("project-requested", None)
    assert [item.args[2] for item in has_access.call_args_list] == [
        "project-stored",
        "project-requested",
    ]


def test_send_message_rejects_existing_project_chat_without_project_access():
    db = _FakeDb(chat=Chats(id="chat-1", user_id="user-1", project_id="project-1", meta={}))

    with patch("app.projects.models.has_project_access", return_value=False):
        stream = chat_utils.send_message(
            "user-1",
            "group-1",
            "chat-1",
            "hello",
            None,
            None,
            None,
            None,
            None,
            None,
            "model-1",
            None,
            {},
            db,
        )

        with pytest.raises(HTTPException) as exc_info:
            next(stream)

    assert exc_info.value.status_code == 404


def test_send_message_aborts_before_stream_start_when_user_message_persistence_fails(monkeypatch):
    chat, start_calls = _patch_send_message_basics(monkeypatch)
    db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None, rollback=lambda: None)

    def _raise_create_chat_message(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_utils, "create_chat_message", _raise_create_chat_message)

    stream = chat_utils.send_message(
        "user-1",
        "group-1",
        None,
        "hello world",
        None,
        None,
        None,
        None,
        None,
        None,
        "model-1",
        None,
        {},
        db,
    )

    with pytest.raises(HTTPException) as exc_info:
        next(stream)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to persist chat message"
    assert start_calls == []
    chat_utils._cleanup_chat_after_empty_transcript.assert_called_once_with(chat, "group-1", db)
    chat_utils.finalize_rate_limit_admission.assert_called_once()


def test_send_message_aborts_saved_temp_chat_when_history_persistence_fails(monkeypatch):
    chat, start_calls = _patch_send_message_basics(monkeypatch, save_temp=True)
    db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None, rollback=lambda: None)

    def _raise_create_chat_message(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_utils, "create_chat_message", _raise_create_chat_message)

    stream = chat_utils.send_message(
        "user-1",
        "group-1",
        None,
        "latest turn",
        None,
        None,
        None,
        None,
        None,
        '[{"id":"temp-1","role":"user","content":"earlier"}]',
        "model-1",
        None,
        {},
        db,
    )

    with pytest.raises(HTTPException) as exc_info:
        next(stream)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to persist chat message"
    assert start_calls == []
    chat_utils._cleanup_chat_after_empty_transcript.assert_called_once_with(chat, "group-1", db)
    chat_utils.finalize_rate_limit_admission.assert_called_once()


def test_unsaved_temp_generation_has_no_synthetic_chat_mapping(monkeypatch):
    """Temporary generations rely on their user-owned ID for cancellation."""

    recorded_calls = {}
    _chat, start_calls = _patch_send_message_basics(
        monkeypatch,
        save_temp=False,
        recorded_calls=recorded_calls,
    )
    db = SimpleNamespace(commit=lambda: None, refresh=lambda obj: None, rollback=lambda: None)

    # The router reserves client-created IDs before entering send_message.
    assert chat_utils.cancel_registry.reserve("generation-temp", "user-1") is True

    stream = chat_utils.send_message(
        "user-1",
        "group-1",
        None,
        "temporary turn",
        None,
        None,
        None,
        None,
        None,
        "[]",
        "model-1",
        None,
        {},
        db,
        generation_id="generation-temp",
    )

    assert next(stream) == '{"t": "s", "d": "generation-temp"}\n'
    assert start_calls == [(("generation-temp", ""), {})]
    assert chat_utils.cancel_registry.is_owned_by("generation-temp", "user-1") is True
    assert recorded_calls["create_chat"] == []
    assert recorded_calls["set_active"] == []
    stream.close()
