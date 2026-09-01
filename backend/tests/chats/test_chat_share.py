import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
    for attr_name in (
        "short",
        "ushort",
        "intc",
        "uintc",
        "int_",
        "uint",
        "longlong",
        "ulonglong",
        "half",
        "float16",
        "float32",
        "float64",
        "single",
        "double",
        "longdouble",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "intp",
        "uintp",
        "bool_",
        "integer",
        "floating",
        "generic",
        "number",
        "ndarray",
    ):
        setattr(fake_numpy, attr_name, int if "float" not in attr_name and attr_name != "bool_" else float)
    fake_numpy.bool_ = bool
    fake_numpy.integer = int
    fake_numpy.floating = float
    fake_numpy.generic = object
    fake_numpy.number = (int, float)
    fake_numpy.ndarray = list
    sys.modules["numpy"] = fake_numpy

if "numpy.typing" not in sys.modules:
    sys.modules["numpy.typing"] = ModuleType("numpy.typing")

if "pandas" not in sys.modules:
    fake_pandas = ModuleType("pandas")
    fake_pandas.DataFrame = type("DataFrame", (), {})
    fake_pandas.to_datetime = lambda value, *args, **kwargs: value
    fake_pandas.isna = lambda value: False
    sys.modules["pandas"] = fake_pandas

if "elevenlabs" not in sys.modules:
    fake_elevenlabs = ModuleType("elevenlabs")
    fake_elevenlabs.SpeechToTextConvertRequestModelId = "scribe_v1"
    sys.modules["elevenlabs"] = fake_elevenlabs

if "elevenlabs.client" not in sys.modules:
    fake_elevenlabs_client = ModuleType("elevenlabs.client")
    fake_elevenlabs_client.ElevenLabs = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["elevenlabs.client"] = fake_elevenlabs_client

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")
    fake_markitdown.MarkItDown = type("MarkItDown", (), {"__init__": lambda self, *args, **kwargs: None})
    sys.modules["markitdown"] = fake_markitdown

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

if "app.tools.websearch.domain_filters" not in sys.modules:
    fake_domain_filters = ModuleType("app.tools.websearch.domain_filters")
    fake_domain_filters.normalize_domain_list = lambda value: []
    fake_domain_filters.resolve_websearch_provider_domain_filters = lambda *args, **kwargs: {}
    fake_domain_filters.filter_scraped_webpages_by_domains = lambda pages, *args, **kwargs: pages
    fake_domain_filters.__getattr__ = lambda _name: (lambda *args, **kwargs: [] if args else None)
    sys.modules["app.tools.websearch.domain_filters"] = fake_domain_filters

from app.chats import utils as chat_utils
from app.chats import router as chat_router
from app.chats.models import Chats
from app.utils.cache_headers import NO_STORE_HEADERS


class _FakeQuery:
    def __init__(self, result=None, rows=None):
        self._result = result
        self._rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, chat=None, rows=None):
        self.chat = chat
        self.rows = rows or []
        self.commits = 0

    def query(self, model):
        if model is Chats:
            return _FakeQuery(self.chat)
        return _FakeQuery(rows=self.rows)

    def commit(self):
        self.commits += 1


def _chat(**overrides):
    values = {
        "id": "chat-1",
        "user_id": "user-1",
        "title": "Shared title",
        "share_id": "share-1",
        "share": {"password": None, "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None},
        "meta": {},
        "last_updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _row(**overrides):
    values = {
        "id": "msg-1",
        "role": "assistant",
        "content": "hello",
        "reference_id": None,
        "retry_count": 0,
        "created_at": datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc),
        "thinking": "secret reasoning",
        "generation": {"provider": "internal"},
        "meta": {"private": True},
        "model_id": "model-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _share_request(headers=None):
    return SimpleNamespace(
        headers=headers or {"user-agent": "pytest"},
        client=SimpleNamespace(host="203.0.113.10"),
    )


class ChatShareTests:
    def setup_method(self):
        chat_utils._SHARE_PASSWORD_ATTEMPTS.clear()

    def test_chat_share_policy_uses_only_chat_sharing_setting(self):
        """Chat sharing is governed solely by its dedicated group setting."""

        # A single lookup is intentional: the removed chat-availability toggle
        # must not be consulted as an additional prerequisite for sharing.
        db = MagicMock()
        with patch.object(chat_utils, "get_user_group_setting_value", return_value=True) as get_setting:
            chat_utils.ensure_chat_sharing_enabled_for_user("user-1", db)

        get_setting.assert_called_once_with(
            "user-1",
            "sharing",
            "enable_chat_sharing",
            db,
        )

    def test_public_serializer_strips_internal_fields_and_reasoning(self):
        messages = [
            _row(
                id="u1",
                role="user",
                content=json.dumps([
                    {
                        "type": "user",
                        "content": "show image",
                        "images": [{"id": "file-1", "meta": {"original_filename": "a.png", "mime_type": "image/png"}}],
                        "chat_references": [{"chat_id": "private-chat", "title": "Other chat"}],
                    }
                ]),
            ),
            _row(
                id="a1",
                role="assistant",
                reference_id="u1",
                content=json.dumps([
                    {"type": "reasoning", "content": "hidden chain"},
                    {"type": "tool_call", "content": "hidden args", "meta": {"tool_args": {"q": "private"}}},
                    {"type": "content", "content": "answer", "meta": {"citations": [{"url": "https://example.test"}], "private": "drop"}},
                    {"type": "file", "documents": [{"id": "file-2", "meta": {"original_filename": "report.pdf", "mime_type": "application/pdf"}}]},
                ]),
            ),
        ]

        def file_lookup(file_id):
            return {
                "file_name": "a.png" if file_id == "file-1" else "report.pdf",
                "file_type": "image/png" if file_id == "file-1" else "application/pdf",
                "file_size": None,
                "meta": {
                    "original_filename": "a.png" if file_id == "file-1" else "report.pdf",
                    "mime_type": "image/png" if file_id == "file-1" else "application/pdf",
                },
            }

        payload = chat_utils._serialize_public_chat_rows(messages, file_lookup)

        assert len(payload) == 2
        assert payload[0]["id"] == "shared-msg-1"
        assert payload[1]["id"] == "shared-msg-2"
        assert payload[1]["reference_id"] == "shared-msg-1"
        assert "model_id" not in payload[0]
        assert "thinking" not in payload[1]
        assert "generation" not in payload[1]
        assert "meta" not in payload[1]
        user_block = payload[0]["content"][0]
        assert user_block["type"] == "user"
        assert user_block["content"] == "show image"
        assert "chat_references" not in user_block
        assert user_block["images"][0]["id"] == "file-1"
        assert user_block["images"][0]["original_filename"] == "a.png"
        assert user_block["images"][0]["mime_type"] == "image/png"
        assert [block["type"] for block in payload[1]["content"]] == ["share_omission", "content", "file"]
        assert payload[1]["content"][0]["reason"] == "tool_activity_not_published"
        assert payload[1]["content"][1]["meta"] == {"citations": [{"url": "https://example.test"}]}

    def test_shared_access_transmits_only_the_owner_selected_response_version(self):
        chat = _chat(
            share={
                "password": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "expires_at": None,
                "publication": {
                    "schema_version": 1,
                    "response_versions": {"u1": "a1"},
                    "approved_output_ids": [],
                },
            }
        )
        rows = [
            _row(id="u1", role="user", content="question"),
            _row(id="a0", reference_id="u1", retry_count=0, content="superseded secret"),
            _row(id="a1", reference_id="u1", retry_count=1, content="reviewed answer"),
        ]
        db = MagicMock()
        token_exp = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils, "_get_ordered_chat_rows", return_value=rows
        ), patch.object(chat_utils, "_build_file_lookup_for_user", return_value=lambda _file_id: None), patch.object(
            chat_utils, "create_chat_share_access_token", return_value=("access-token", token_exp)
        ):
            payload = chat_utils.get_shared_chat_messages("share-1", None, db)

        serialized = json.dumps(payload)
        assert "reviewed answer" in serialized
        assert "superseded secret" not in serialized
        assert len(payload["messages"]) == 2
        assert payload["messages"][1]["content"] == "reviewed answer"
        assert payload["messages"][1]["total_versions"] == 1

    def test_legacy_share_defaults_to_latest_response_without_transmitting_older_versions(self):
        rows = [
            _row(id="u1", role="user", content="question"),
            _row(id="a0", reference_id="u1", retry_count=0, content="old answer"),
            _row(id="a1", reference_id="u1", retry_count=1, content="latest answer"),
        ]

        selected = chat_utils._select_public_chat_rows(rows, {})

        assert [row.id for row in selected] == ["u1", "a1"]

    def test_reviewed_share_does_not_replace_a_missing_original_with_a_regeneration(self):
        rows = [
            _row(id="u1", role="user", content="question"),
            _row(id="a1", reference_id="u1", retry_count=1, content="unreviewed regeneration"),
        ]
        share = {
            "publication": {
                "schema_version": 1,
                "response_versions": {},
                "approved_output_ids": [],
            }
        }

        selected = chat_utils._select_public_chat_rows(rows, share)
        options = chat_utils._build_share_publication_options(rows, share)

        assert [row.id for row in selected] == ["u1"]
        assert options["publication"]["response_versions"] == {}
        assert options["turns"][0]["versions"][0]["selected"] is False

    def test_reviewed_quiz_is_static_and_unreviewed_widgets_get_an_omission_marker(self):
        quiz_block = {
            "type": "widget",
            "content": "<script>privateWidget()</script>",
            "meta": {
                "widget_type": "quiz",
                "tool_result": {
                    "title": "Safety review",
                    "description": "Choose carefully",
                    "questions": [
                        {
                            "question": "Publish raw widget HTML?",
                            "options": ["Yes", "No", "Maybe", "Always"],
                            "correct_option_index": 1,
                            "explanation": "Static structured output is safer.",
                        }
                    ],
                },
            },
        }
        rows = [
            _row(id="u1", role="user", content="make a quiz"),
            _row(id="a1", reference_id="u1", content=json.dumps([quiz_block])),
        ]

        options = chat_utils._build_share_publication_options(rows, {})
        output_id = options["turns"][0]["versions"][0]["static_outputs"][0]["id"]
        unreviewed = chat_utils._serialize_public_chat_rows(rows, lambda _file_id: None)
        reviewed = chat_utils._serialize_public_chat_rows(
            rows,
            lambda _file_id: None,
            approved_output_ids={output_id},
        )

        assert unreviewed[1]["content"] == [
            {"type": "share_omission", "reason": "interactive_output_not_published"}
        ]
        static_output = reviewed[1]["content"][0]
        assert static_output["type"] == "shared_tool_output"
        assert static_output["output_type"] == "quiz"
        assert static_output["title"] == "Safety review"
        assert static_output["items"][0]["answer"] == "No"
        assert "privateWidget" not in json.dumps(reviewed)

    def test_tool_role_messages_become_timeline_omission_notices(self):
        rows = [
            _row(id="u1", role="user", content="run it"),
            _row(id="t1", role="tool", content="private tool payload"),
        ]

        payload = chat_utils._serialize_public_chat_rows(rows, lambda _file_id: None)

        assert payload[1]["role"] == "share_notice"
        assert payload[1]["content"] == [
            {"type": "share_omission", "reason": "tool_message_not_published"}
        ]
        assert "private tool payload" not in json.dumps(payload)

    def test_publication_update_persists_valid_choices_and_rejects_cross_turn_versions(self):
        chat = _chat()
        rows = [
            _row(id="u1", role="user", content="first"),
            _row(id="a0", reference_id="u1", retry_count=0, content="old"),
            _row(id="a1", reference_id="u1", retry_count=1, content="new"),
            _row(id="u2", role="user", content="second"),
            _row(id="a2", reference_id="u2", retry_count=0, content="second answer"),
        ]
        db = MagicMock()

        with patch.object(chat_utils, "get_chat", return_value=chat), patch.object(
            chat_utils, "_get_ordered_chat_rows", return_value=rows
        ), patch.object(chat_utils, "ensure_chat_sharing_enabled_or_existing_share"), patch.object(
            chat_utils, "get_public_url", return_value="https://chat.example"
        ):
            result = chat_utils.update_share_publication(
                "user-1",
                "chat-1",
                {"response_versions": {"u1": "a0", "u2": "a2"}, "approved_output_ids": []},
                db,
            )
            assert result["publication"]["response_versions"] == {"u1": "a0", "u2": "a2"}
            assert chat.share["publication"]["schema_version"] == 1

            with pytest.raises(HTTPException) as incomplete:
                chat_utils.update_share_publication(
                    "user-1",
                    "chat-1",
                    {"response_versions": {"u1": "a0"}, "approved_output_ids": []},
                    db,
                )

            with pytest.raises(HTTPException) as invalid:
                chat_utils.update_share_publication(
                    "user-1",
                    "chat-1",
                    {"response_versions": {"u1": "a2"}, "approved_output_ids": []},
                    db,
                )

        assert invalid.value.status_code == 400
        assert incomplete.value.status_code == 400

    def test_shared_file_access_uses_the_selected_response_version(self):
        chat = _chat(
            share={
                "password": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "expires_at": None,
                "publication": {
                    "schema_version": 1,
                    "response_versions": {"u1": "a1"},
                    "approved_output_ids": [],
                },
            }
        )
        rows = [
            _row(id="u1", role="user", content="question"),
            _row(
                id="a0",
                reference_id="u1",
                retry_count=0,
                content=json.dumps([{"type": "file", "documents": [{"id": "old-file"}]}]),
            ),
            _row(
                id="a1",
                reference_id="u1",
                retry_count=1,
                content=json.dumps([{"type": "file", "documents": [{"id": "new-file"}]}]),
            ),
        ]
        db = MagicMock()

        with patch.object(
            chat_utils,
            "verify_chat_share_access_token",
            return_value=("share-1", chat_utils._share_password_fingerprint(None)),
        ), patch.object(
            chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)
        ), patch.object(chat_utils, "_get_ordered_chat_rows", return_value=rows), patch.object(
            chat_utils, "ensure_chat_share_file_access_enabled_for_user"
        ), patch.object(chat_utils, "get_file_info", return_value={"id": "new-file"}):
            access = chat_utils.resolve_shared_chat_file_access("token", "new-file", db)
            assert access["file_id"] == "new-file"

            with pytest.raises(HTTPException) as hidden:
                chat_utils.resolve_shared_chat_file_access("token", "old-file", db)

        assert hidden.value.status_code == 404

    def test_shared_access_returns_public_payload_and_unchanged_response(self):
        chat = _chat()
        db = MagicMock()
        token_exp = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "_get_ordered_chat_rows",
            return_value=[_row(id="u1", role="user", content="hello")],
        ) as mock_rows, patch.object(chat_utils, "_build_file_lookup_for_user", return_value=lambda _file_id: None), patch.object(
            chat_utils,
            "create_chat_share_access_token",
            return_value=("access-token", token_exp),
        ):
            payload = chat_utils.get_shared_chat_messages("share-1", None, db)
            unchanged = chat_utils.get_shared_chat_messages(
                "share-1",
                None,
                db,
                known_updated_at=payload["updated_at"],
            )

        assert payload["unchanged"] is False
        assert payload["access_mode"] == "public"
        assert payload["messages"][0]["content"] == "hello"
        assert unchanged["unchanged"] is True
        assert unchanged["access_mode"] == "public"
        assert "messages" not in unchanged
        assert unchanged["share_access_token"] == "access-token"
        assert mock_rows.call_count == 1

    def test_shared_access_route_sets_no_store_headers(self):
        payload = chat_router.AccessSharedChatRequest(share_id="share-1")
        header_response = chat_router.Response()
        result = {
            "title": "Shared title",
            "messages": [],
            "access_mode": "public",
            "has_password": False,
            "unchanged": False,
        }

        with patch.object(
            chat_router,
            "_shared_chat_audit_subject",
            return_value={"share_id": "share-1", "chat_id": "chat-1", "owner_user_id": "user-1"},
        ), patch.object(chat_router, "extract_client_ip_from_request", return_value="203.0.113.10"), patch.object(
            chat_router,
            "resolve_trusted_proxy_networks",
            return_value=[],
        ), patch.object(chat_router, "get_shared_chat_messages", return_value=result), patch.object(
            chat_router,
            "_log_chat_share_event",
        ):
            response = chat_router.access_shared_chat_route(
                payload=payload,
                request=_share_request(),
                response=header_response,
                db=MagicMock(),
                db_log=MagicMock(),
            )

        assert response is result
        for name, value in NO_STORE_HEADERS.items():
            assert header_response.headers[name] == value

    def test_shared_file_route_sets_no_store_headers_on_download_response(self):
        download_response = chat_router.Response(content=b"file-bytes")

        with patch.object(chat_router, "extract_client_ip_from_request", return_value="203.0.113.10"), patch.object(
            chat_router,
            "resolve_trusted_proxy_networks",
            return_value=[],
        ), patch.object(
            chat_router,
            "resolve_shared_chat_file_access",
            return_value={"share_id": "share-1", "user_id": "user-1", "file_id": "file-1"},
        ), patch.object(
            chat_router,
            "download_file",
            return_value=download_response,
        ), patch.object(chat_router, "_log_chat_share_event"):
            response = chat_router.access_shared_chat_file_route(
                file_id="file-1",
                request=_share_request(headers={"Authorization": "Bearer share-token", "user-agent": "pytest"}),
                inline=True,
                db=MagicMock(),
                db_log=MagicMock(),
            )

        assert response is download_response
        for name, value in NO_STORE_HEADERS.items():
            assert response.headers[name] == value

    def test_authenticated_access_mode_requires_valid_user_for_transcript_and_files(self):
        chat = _chat(share={"password": None, "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None, "access_mode": "authenticated"})
        row = _row(role="assistant", content=json.dumps([{"type": "content", "content": "file", "documents": [{"id": "file-1"}]}]))
        db = MagicMock()
        token_exp = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "check_user_by_token",
            side_effect=HTTPException(status_code=401, detail="bad token"),
        ):
            with pytest.raises(HTTPException) as missing_auth:
                chat_utils.get_shared_chat_messages("share-1", None, db)
            assert missing_auth.value.status_code == 401
            assert missing_auth.value.detail == "Authentication required"

            with pytest.raises(HTTPException) as invalid_auth:
                chat_utils.get_shared_chat_messages("share-1", None, db, user_access_token="bad-token")
            assert invalid_auth.value.status_code == 401
            assert invalid_auth.value.detail == "Authentication required"

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "check_user_by_token",
            return_value=SimpleNamespace(id="viewer-1"),
        ), patch.object(chat_utils, "_get_ordered_chat_rows", return_value=[]), patch.object(
            chat_utils,
            "_build_file_lookup_for_user",
            return_value=lambda _file_id: None,
        ), patch.object(chat_utils, "create_chat_share_access_token", return_value=("access-token", token_exp)):
            payload = chat_utils.get_shared_chat_messages("share-1", None, db, user_access_token="viewer-token")
        assert payload["access_mode"] == "authenticated"

        with patch.object(chat_utils, "verify_chat_share_access_token", return_value=("share-1", chat_utils._share_password_fingerprint(None))), patch.object(
            chat_utils,
            "_resolve_shared_chat_or_404",
            return_value=(chat, chat.share),
        ), patch.object(chat_utils, "check_user_by_token", side_effect=HTTPException(status_code=401, detail="bad token")):
            with pytest.raises(HTTPException) as missing_file_auth:
                chat_utils.resolve_shared_chat_file_access("share-token", "file-1", db)
            assert missing_file_auth.value.status_code == 401

        with patch.object(chat_utils, "verify_chat_share_access_token", return_value=("share-1", chat_utils._share_password_fingerprint(None))), patch.object(
            chat_utils,
            "_resolve_shared_chat_or_404",
            return_value=(chat, chat.share),
        ), patch.object(chat_utils, "check_user_by_token", return_value=SimpleNamespace(id="viewer-1")), patch.object(
            chat_utils,
            "_get_ordered_chat_rows",
            return_value=[row],
        ), patch.object(chat_utils, "ensure_chat_share_file_access_enabled_for_user"), patch.object(
            chat_utils,
            "get_file_info",
            return_value={"id": "file-1"},
        ):
            access = chat_utils.resolve_shared_chat_file_access("share-token", "file-1", db, user_access_token="viewer-token")
        assert access == {"share_id": "share-1", "user_id": "user-1", "file_id": "file-1"}

    def test_shared_access_password_required_invalid_and_throttled(self):
        chat = _chat(share={"password": "hash", "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None})
        db = MagicMock()

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "verify_password",
            return_value=False,
        ), patch.object(chat_utils, "get_redis_client", return_value=None):
            with pytest.raises(HTTPException) as required:
                chat_utils.get_shared_chat_messages("share-1", None, db, client_ip="203.0.113.10")
            assert required.value.status_code == 401
            assert required.value.detail == "Password required"

            for _ in range(chat_utils.CHAT_SHARE_PASSWORD_ATTEMPT_LIMIT):
                with pytest.raises(HTTPException) as invalid:
                    chat_utils.get_shared_chat_messages("share-1", "wrong-password", db, client_ip="203.0.113.10")
                assert invalid.value.status_code == 401

            with pytest.raises(HTTPException) as limited:
                chat_utils.get_shared_chat_messages("share-1", "wrong-password", db, client_ip="203.0.113.10")
            assert limited.value.status_code == 429

    def test_valid_password_clears_failed_attempts(self):
        chat = _chat(share={"password": "hash", "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None})
        db = MagicMock()
        token_exp = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "_get_ordered_chat_rows",
            return_value=[],
        ), patch.object(chat_utils, "_build_file_lookup_for_user", return_value=lambda _file_id: None), patch.object(
            chat_utils,
            "create_chat_share_access_token",
            return_value=("access-token", token_exp),
        ), patch.object(chat_utils, "get_redis_client", return_value=None), patch.object(
            chat_utils,
            "verify_password",
            side_effect=[False, True],
        ):
            with pytest.raises(HTTPException):
                chat_utils.get_shared_chat_messages("share-1", "wrong-password", db, client_ip="203.0.113.10")

            chat_utils.get_shared_chat_messages("share-1", "correct-password", db, client_ip="203.0.113.10")

        assert chat_utils._SHARE_PASSWORD_ATTEMPTS == {}

    def test_share_access_token_bypasses_repeated_password_prompt(self):
        chat = _chat(share={"password": "hash", "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None})
        db = MagicMock()
        token_exp = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "verify_chat_share_access_token",
            return_value=("share-1", chat_utils._share_password_fingerprint("hash")),
        ), patch.object(chat_utils, "verify_password", side_effect=AssertionError("password should not be checked")), patch.object(
            chat_utils,
            "_get_ordered_chat_rows",
            return_value=[],
        ), patch.object(chat_utils, "_build_file_lookup_for_user", return_value=lambda _file_id: None), patch.object(
            chat_utils,
            "create_chat_share_access_token",
            return_value=("fresh-access-token", token_exp),
        ):
            payload = chat_utils.get_shared_chat_messages(
                "share-1",
                None,
                db,
                share_access_token="existing-access-token",
            )

        assert payload["share_access_token"] == "fresh-access-token"
        assert payload["has_password"] is True

    def test_password_create_change_remove_and_minimum_policy(self):
        chat = _chat()
        db = MagicMock()

        with patch.object(chat_utils, "ensure_chat_sharing_enabled_for_user"), patch.object(
            chat_utils,
            "get_chat",
            return_value=chat,
        ), patch.object(chat_utils, "get_public_url", return_value="https://chat.example"), patch.object(
            chat_utils,
            "hash_password",
            side_effect=lambda value: f"hash:{value}",
        ), patch.object(
            chat_utils.uuid,
            "uuid4",
            side_effect=["share-created", "share-fresh"],
        ):
            with pytest.raises(HTTPException) as short_create:
                chat_utils.share_chat("user-1", "chat-1", "short", db)
            assert short_create.value.status_code == 400

            created = chat_utils.share_chat("user-1", "chat-1", "long-password", db)
            assert created["share_id"] == "share-created"
            assert created["has_password"] is True
            assert created["access_mode"] == "public"
            assert chat.share_id == "share-created"
            assert chat.share["password"] == "hash:long-password"

            authenticated = chat_utils.share_chat("user-1", "chat-1", None, db, access_mode="authenticated")
            assert authenticated["share_id"] == "share-fresh"
            assert authenticated["access_mode"] == "authenticated"
            assert authenticated["has_password"] is False
            assert chat.share_id == "share-fresh"
            assert chat.share["access_mode"] == "authenticated"
            assert chat.share["password"] is None

            public = chat_utils.update_share_access_mode("user-1", "chat-1", "public", db)
            assert public["access_mode"] == "public"
            assert chat.share["access_mode"] == "public"

            with pytest.raises(HTTPException) as invalid_mode:
                chat_utils.update_share_access_mode("user-1", "chat-1", "private", db)
            assert invalid_mode.value.status_code == 400

            with pytest.raises(HTTPException) as short_change:
                chat_utils.update_share_password("user-1", "chat-1", "short", db, action="change")
            assert short_change.value.status_code == 400

            changed = chat_utils.update_share_password("user-1", "chat-1", "new-password", db, action="change")
            assert changed["has_password"] is True
            assert chat.share["password"] == "hash:new-password"

            removed = chat_utils.update_share_password("user-1", "chat-1", None, db, action="remove")
            assert removed["has_password"] is False
            assert chat.share["password"] is None

    def test_share_chat_blocks_existing_share_updates_when_sharing_disabled(self):
        chat = _chat(share_id="existing-share")
        db = MagicMock()

        with patch.object(
            chat_utils,
            "ensure_chat_sharing_enabled_for_user",
            side_effect=HTTPException(status_code=403, detail="disabled"),
        ), patch.object(
            chat_utils,
            "get_chat",
            return_value=chat,
        ), patch.object(chat_utils, "get_public_url", return_value="https://chat.example"), patch.object(
            chat_utils.uuid,
            "uuid4",
            side_effect=AssertionError("disabled existing shares must not rotate"),
        ):
            with pytest.raises(HTTPException) as blocked:
                chat_utils.share_chat("user-1", "chat-1", None, db)

        assert blocked.value.status_code == 403
        assert blocked.value.detail == "Share updates blocked by policy"
        assert chat.share_id == "existing-share"
        db.commit.assert_not_called()

    @pytest.mark.parametrize(
        "meta",
        [
            {"shadow_deleted": True},
            {"status": "temp"},
            '{"shadow_deleted": true}',
            '{"status": "temp"}',
        ],
    )
    def test_share_chat_rejects_deleted_and_temporary_chats(self, meta):
        chat = _chat(meta=meta)
        db = MagicMock()

        with patch.object(chat_utils, "ensure_chat_sharing_enabled_for_user"), patch.object(
            chat_utils,
            "get_chat",
            return_value=chat,
        ):
            with pytest.raises(HTTPException) as exc_info:
                chat_utils.share_chat("user-1", "chat-1", None, db)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Chat not found"

    def test_expiry_cleanup_revoke_and_group_disabled(self):
        expired_chat = _chat(
            share={"password": None, "created_at": "2026-01-01T00:00:00+00:00", "expires_at": "2000-01-01T00:00:00+00:00"}
        )
        db = _FakeDb(chat=expired_chat)

        with patch.object(chat_utils, "ensure_chat_sharing_enabled_for_user"), patch.object(
            chat_utils,
            "get_chat",
            return_value=expired_chat,
        ):
            status = chat_utils.get_share_status("user-1", "chat-1", db)
            assert status["share_id"] is None
            assert expired_chat.share_id is None
            assert db.commits == 1

            expired_chat.share_id = "share-1"
            expired_chat.share = {"password": None, "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None}
            result = chat_utils.delete_chat_share("user-1", "chat-1", db)
            assert result == {"ok": True}
            assert expired_chat.share is None
            assert expired_chat.share_id is None

        disabled_chat = _chat(share_id="share-disabled")
        disabled_db = _FakeDb(chat=disabled_chat)
        with patch.object(
            chat_utils,
            "ensure_chat_sharing_enabled_for_user",
            side_effect=AssertionError("share policy should not gate existing shares"),
        ):
            resolved_chat, _ = chat_utils._resolve_shared_chat_or_404(disabled_db, "share-disabled")
            assert resolved_chat is disabled_chat

        shadow_deleted_chat = _chat(meta={"shadow_deleted": True})
        shadow_deleted_db = _FakeDb(chat=shadow_deleted_chat)
        with patch.object(chat_utils, "ensure_chat_sharing_enabled_for_user"):
            with pytest.raises(HTTPException) as shadow_deleted:
                chat_utils._resolve_shared_chat_or_404(shadow_deleted_db, "share-1")
            assert shadow_deleted.value.status_code == 404

    def test_invite_users_to_chat_creates_invited_share_notifications(self):
        chat = _chat(title="Roadmap planning")
        invited_user = SimpleNamespace(
            id="user-2",
            is_active=True,
            role="user",
            deleted_at=None,
            settings={"security": {"profile_visibility": "public"}},
        )
        inactive_user = SimpleNamespace(
            id="user-3",
            is_active=False,
            role="user",
            deleted_at=None,
            settings={"security": {"profile_visibility": "public"}},
        )
        db = _FakeDb(chat=chat, rows=[invited_user, inactive_user])
        db_log = MagicMock()
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})
        owner = SimpleNamespace(id="user-1", first_name="Ada", last_name="Lovelace", email="ada@example.com", group_id="team-a")
        payload = chat_router.InviteChatUsersRequest(chat_id="chat-1", user_ids=["user-1", "user-2", "user-2", "user-3"])

        with patch.object(
            chat_router,
            "share_chat",
            return_value={
                "share_id": "share-invite",
                "share_url": "https://chat.example/chats/shared/share-invite",
                "access_mode": "invited",
                "created_at": "2026-01-01T00:00:00+00:00",
                "expires_at": None,
                "invited_user_ids": ["user-2"],
            },
        ) as share_mock, patch.object(chat_router, "create_user_notification") as notification_mock, patch.object(
            chat_router,
            "create_audit_log",
        ):
            result = chat_router.invite_users_to_chat_route(payload, request, db, db_log, owner)

        assert result.share_id == "share-invite"
        assert result.access_mode == "invited"
        assert result.invited_count == 1
        assert result.invited_user_ids == ["user-2"]
        share_mock.assert_called_once()
        assert share_mock.call_args.args[:4] == ("user-1", "chat-1", None, db)
        assert share_mock.call_args.args[4] is None
        assert share_mock.call_args.kwargs == {"access_mode": "invited", "invited_user_ids": ["user-2"]}
        notification_mock.assert_called_once()
        notification = notification_mock.call_args.kwargs
        assert notification["user_ids"] == ["user-2"]
        assert notification["details"]["item_type"] == "chat"
        assert notification["details"]["share_type"] == "invited"
        assert notification["details"]["share_id"] == "share-invite"

    def test_invite_users_to_chat_rejects_unavailable_selected_users(self):
        chat = _chat(title="Roadmap planning")
        db = _FakeDb(chat=chat, rows=[])
        db_log = MagicMock()
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})
        owner = SimpleNamespace(id="user-1", first_name="Ada", last_name="Lovelace", email="ada@example.com", group_id="team-a")
        payload = chat_router.InviteChatUsersRequest(chat_id="chat-1", user_ids=["user-2"])

        with patch.object(
            chat_router,
            "resolve_invitable_users_for_sharing",
            side_effect=HTTPException(status_code=400, detail="One or more selected users are no longer available to invite"),
        ), patch.object(chat_router, "share_chat") as share_mock:
            with pytest.raises(HTTPException) as exc_info:
                chat_router.invite_users_to_chat_route(payload, request, db, db_log, owner)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "One or more selected users are no longer available to invite"
        share_mock.assert_not_called()

    def test_invited_access_mode_requires_invited_user_for_transcript(self):
        chat = _chat(
            user_id="owner-1",
            share={
                "password": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "expires_at": None,
                "access_mode": "invited",
                "owner_user_id": "owner-1",
                "invited_user_ids": ["viewer-1"],
            },
        )
        db = MagicMock()
        token_exp = datetime(2026, 1, 2, 0, 15, tzinfo=timezone.utc)

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "check_user_by_token",
            return_value=SimpleNamespace(id="other-1"),
        ):
            with pytest.raises(HTTPException) as denied:
                chat_utils.get_shared_chat_messages("share-1", None, db, user_access_token="other-token")
            assert denied.value.status_code == 403

        with patch.object(chat_utils, "_resolve_shared_chat_or_404", return_value=(chat, chat.share)), patch.object(
            chat_utils,
            "check_user_by_token",
            return_value=SimpleNamespace(id="viewer-1"),
        ), patch.object(chat_utils, "_get_ordered_chat_rows", return_value=[]), patch.object(
            chat_utils,
            "_build_file_lookup_for_user",
            return_value=lambda _file_id: None,
        ), patch.object(chat_utils, "create_chat_share_access_token", return_value=("access-token", token_exp)):
            payload = chat_utils.get_shared_chat_messages("share-1", None, db, user_access_token="viewer-token")

        assert payload["access_mode"] == "invited"

    def test_shared_file_token_access_and_revocation_by_password_fingerprint(self):
        chat = _chat(share={"password": "hash", "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None})
        row = _row(
            role="assistant",
            content=json.dumps([
                {"type": "reasoning", "content": "hidden", "documents": [{"id": "hidden-file"}]},
                {"type": "content", "content": "file", "documents": [{"id": "file-1"}]},
            ]),
        )
        db = MagicMock()

        with patch.object(chat_utils, "verify_chat_share_access_token", return_value=("share-1", chat_utils._share_password_fingerprint("hash"))), patch.object(
            chat_utils,
            "_resolve_shared_chat_or_404",
            return_value=(chat, chat.share),
        ), patch.object(chat_utils, "_get_ordered_chat_rows", return_value=[row]), patch.object(
            chat_utils,
            "ensure_chat_share_file_access_enabled_for_user",
        ), patch.object(chat_utils, "get_file_info", return_value={"id": "file-1"}):
            access = chat_utils.resolve_shared_chat_file_access("token", "file-1", db)

        assert access == {"share_id": "share-1", "user_id": "user-1", "file_id": "file-1"}

        with patch.object(chat_utils, "verify_chat_share_access_token", return_value=("share-1", "old-fingerprint")), patch.object(
            chat_utils,
            "_resolve_shared_chat_or_404",
            return_value=(chat, chat.share),
        ):
            with pytest.raises(HTTPException) as exc_info:
                chat_utils.resolve_shared_chat_file_access("token", "file-1", db)
            assert exc_info.value.status_code == 401

        with patch.object(chat_utils, "verify_chat_share_access_token", return_value=("share-1", chat_utils._share_password_fingerprint("hash"))), patch.object(
            chat_utils,
            "_resolve_shared_chat_or_404",
            return_value=(chat, chat.share),
        ), patch.object(chat_utils, "_get_ordered_chat_rows", return_value=[row]), patch.object(
            chat_utils,
            "ensure_chat_share_file_access_enabled_for_user",
        ):
            with pytest.raises(HTTPException) as missing_file:
                chat_utils.resolve_shared_chat_file_access("token", "file-2", db)
            assert missing_file.value.status_code == 404

            with pytest.raises(HTTPException) as hidden_file:
                chat_utils.resolve_shared_chat_file_access("token", "hidden-file", db)
            assert hidden_file.value.status_code == 404
