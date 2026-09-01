import json
import sys
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
import pypdfium2 as pdfium
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
REPO_ROOT = Path(__file__).resolve().parents[3]

from app.chats import compliance as chat_compliance  # noqa: E402
from app.chats import io as chat_io  # noqa: E402
from app.chats.download import export_chat_full  # noqa: E402
from app.chats import download as chat_download  # noqa: E402
from app.chats.models import ChatMessages, Chats  # noqa: E402
from app.users.models import User  # noqa: E402


def _inspect_pdf(content: bytes) -> tuple[str, int]:
    """Return text and image count from trusted PDF export test output."""
    document = pdfium.PdfDocument(content)
    text_parts: list[str] = []
    image_count = 0
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                text_parts.append(text_page.get_text_range())
                image_count += sum(
                    isinstance(page_object, pdfium.PdfImage)
                    for page_object in page.get_objects()
                )
            finally:
                text_page.close()
                page.close()
        return "\n".join(text_parts), image_count
    finally:
        document.close()


class _FakeQuery:
    def __init__(self, result=None, rows=None):
        self._result = result
        self._rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        if self._result is not None:
            return self._result
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, chat=None, chats=None, messages=None, users=None):
        self.chat = chat
        self.chats = chats if chats is not None else ([chat] if chat else [])
        self.messages = messages or []
        self.users = users or []

    def query(self, model):
        if model is Chats:
            return _FakeQuery(result=self.chat, rows=self.chats)
        if model is ChatMessages:
            return _FakeQuery(rows=self.messages)
        if model is User:
            return _FakeQuery(rows=self.users)
        return _FakeQuery()

    def begin_nested(self):
        class _NoopContext:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        return _NoopContext()

    def add(self, _item):
        return None

    def flush(self):
        return None

    def commit(self):
        return None


@pytest.fixture(autouse=True)
def disable_compliance_watermark_lookup(monkeypatch):
    """Keep export unit fixtures focused on rendering unless overridden."""

    monkeypatch.setattr(chat_download, "get_compliance_watermark", lambda *args, **kwargs: "")
    monkeypatch.setattr(chat_compliance, "get_compliance_watermark", lambda *args, **kwargs: "")


def test_full_chat_export_redacts_share_secrets_and_identifiers():
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Shared chat",
        project_id=None,
        share_id="stable-share-id",
        share={
            "password": "HASH_SENTINEL",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-02-01T00:00:00+00:00",
            "access_mode": "invited",
            "owner_user_id": "user-1",
            "invited_user_ids": ["user-2"],
        },
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    exported = export_chat_full("user-1", "chat-1", _FakeDb(chat=chat))

    exported_chat = exported["chat"]
    assert "share_id" not in exported_chat
    assert exported_chat["share"] == {
        "has_password": True,
        "access_mode": "invited",
        "expires_at": "2026-02-01T00:00:00+00:00",
    }
    assert "HASH_SENTINEL" not in str(exported_chat)
    assert "stable-share-id" not in str(exported_chat)
    assert "user-2" not in str(exported_chat)


def test_message_display_content_renders_chat_blocks_without_raw_metadata():
    raw_content = json.dumps(
        [
            {
                "type": "reasoning",
                "content": "private reasoning should not be exported",
                "meta": {"reasoningtime": 1.23},
            },
            {
                "type": "content",
                "content": "Visible answer\n\n- one\n- two",
                "meta": {"model": "example"},
            },
        ]
    )

    rendered = chat_download._message_display_content({"content": raw_content})

    assert rendered == "Visible answer\n\n- one\n- two"
    assert "reasoningtime" not in rendered
    assert "[{" not in rendered


def test_prepare_chat_download_pdf_uses_rendered_content():
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="NVIDIA summary",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="msg-1",
            chat_id="chat-1",
            model_id="model-1",
            role="user",
            content=json.dumps([{"type": "user", "content": "Write text about nvidia"}]),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ChatMessages(
            id="msg-2",
            chat_id="chat-1",
            model_id="model-1",
            role="assistant",
            content=json.dumps(
                [
                    {"type": "reasoning", "content": "hidden thought", "meta": {"reasoningtime": 1.0}},
                    {"type": "content", "content": "NVIDIA is a leading technology company."},
                ]
            ),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]

    result = chat_download.prepare_chat_download("user-1", "chat-1", "pdf", _FakeDb(chat=chat, messages=messages))

    assert result["filename"] == "NVIDIA summary.pdf"
    assert result["type"] == "pdf"
    assert result["content"].startswith(b"%PDF")
    pdf_text, _image_count = _inspect_pdf(result["content"])
    assert "NVIDIA is a leading technology company" in pdf_text
    assert "hidden thought" not in pdf_text
    assert "reasoningtime" not in pdf_text


def test_prepare_chat_download_pdf_renders_generated_files(monkeypatch, tmp_path):
    from PIL import Image

    image_path = tmp_path / "generated_image.png"
    Image.new("RGB", (20, 12), color=(31, 120, 180)).save(image_path)

    image_record = SimpleNamespace(
        id="image-file",
        user_id="user-1",
        file_name="image-file.png",
        file_type="image/png",
        file_category="image",
        file_size=image_path.stat().st_size,
        meta={"original_filename": "generated_image.png"},
    )
    document_record = SimpleNamespace(
        id="doc-file",
        user_id="user-1",
        file_name="doc-file.pdf",
        file_type="application/pdf",
        file_category="document",
        file_size=2048,
        meta={"original_filename": "analysis.pdf"},
    )
    records = {
        "image-file": image_record,
        "doc-file": document_record,
    }

    def fake_resolve_accessible_file_record(db, user_id, file_id):
        record = records.get(file_id)
        return (record, record.user_id) if record else (None, None)

    def fake_materialize_file_record(file_record, owner_user_id):
        assert file_record.id == "image-file"
        assert owner_user_id == "user-1"
        return image_path

    monkeypatch.setattr(chat_download, "resolve_accessible_file_record", fake_resolve_accessible_file_record)
    monkeypatch.setattr(chat_download, "materialize_file_record", fake_materialize_file_record)

    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Generated files",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="msg-1",
            chat_id="chat-1",
            model_id="model-1",
            role="assistant",
            content=json.dumps(
                [
                    {
                        "type": "tool_call_result",
                        "content": "Generated files are ready.",
                        "tool_name": "image_generation",
                        "images": ["image-file"],
                        "documents": ["doc-file"],
                    }
                ]
            ),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]

    result = chat_download.prepare_chat_download("user-1", "chat-1", "pdf", _FakeDb(chat=chat, messages=messages))

    assert result["type"] == "pdf"
    assert result["content"].startswith(b"%PDF")

    pdf_text, embedded_image_count = _inspect_pdf(result["content"])

    assert embedded_image_count >= 1
    assert "generated_image.png" in pdf_text
    assert "analysis.pdf" in pdf_text
    assert "PDF" in pdf_text
    assert "2.00 KB" in pdf_text


@pytest.mark.parametrize("fmt", ["txt", "md"])
def test_prepare_chat_download_text_formats_use_rendered_content(fmt):
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="NVIDIA summary",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="msg-1",
            chat_id="chat-1",
            model_id="model-1",
            role="assistant",
            content=json.dumps(
                [
                    {"type": "reasoning", "content": "hidden thought", "meta": {"reasoningtime": 1.0}},
                    {"type": "content", "content": "Visible answer"},
                ]
            ),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]

    result = chat_download.prepare_chat_download("user-1", "chat-1", fmt, _FakeDb(chat=chat, messages=messages))

    assert "Visible answer" in result["content"]
    assert "hidden thought" not in result["content"]
    assert "reasoningtime" not in result["content"]
    assert "[{" not in result["content"]


@pytest.mark.parametrize("fmt", ["txt", "md", "docx", "pdf"])
def test_prepare_chat_download_formats_apply_compliance_watermark(monkeypatch, fmt):
    """Every rendered download format carries the effective compliance marker."""
    monkeypatch.setattr(
        chat_download,
        "get_compliance_watermark",
        lambda *args, **kwargs: "  Compliance export marker  ",
    )

    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="Watermarked export",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="msg-1",
            chat_id="chat-1",
            model_id="model-1",
            role="assistant",
            content=json.dumps([{"type": "content", "content": "Visible answer"}]),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]

    result = chat_download.prepare_chat_download(
        "user-1",
        "chat-1",
        fmt,
        _FakeDb(chat=chat, messages=messages),
    )

    if fmt in {"txt", "md"}:
        assert result["content"].endswith("Compliance export marker")
    elif fmt == "docx":
        with zipfile.ZipFile(BytesIO(result["content"])) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        assert "Compliance export marker" in document_xml
    else:
        pdf_text, _image_count = _inspect_pdf(result["content"])
        assert "Compliance export marker" in pdf_text


def test_prepare_chat_download_json_adds_display_content_without_removing_raw_content():
    raw_content = json.dumps(
        [
            {"type": "reasoning", "content": "hidden thought", "meta": {"reasoningtime": 1.0}},
            {"type": "content", "content": "Visible answer"},
        ]
    )
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="NVIDIA summary",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="msg-1",
            chat_id="chat-1",
            model_id="model-1",
            role="assistant",
            content=raw_content,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]

    result = chat_download.prepare_chat_download("user-1", "chat-1", "json", _FakeDb(chat=chat, messages=messages))
    message = result["content"]["messages"][0]

    assert result["content"]["application_name"] == "Omlorix"
    assert result["content"]["transcript"]["messages"][0]["content"] == "Visible answer"
    assert message["content"] == raw_content
    assert message["display_content"] == "Visible answer"


def test_prepare_chat_download_json_applies_compliance_watermark_without_mutating_storage(monkeypatch):
    """JSON receives a valid visible block while the persisted message stays unchanged."""
    monkeypatch.setattr(chat_download, "get_compliance_watermark", lambda *args, **kwargs: "Compliance JSON marker")

    raw_content = json.dumps([{"type": "content", "content": "Visible answer"}])
    chat = Chats(id="chat-1", user_id="user-1", title="Watermarked JSON", meta={})
    message = ChatMessages(
        id="msg-1",
        chat_id="chat-1",
        model_id="model-1",
        role="assistant",
        content=raw_content,
    )

    result = chat_download.prepare_chat_download(
        "user-1",
        "chat-1",
        "json",
        _FakeDb(chat=chat, messages=[message]),
    )
    exported_message = result["content"]["messages"][0]
    exported_blocks = json.loads(exported_message["content"])

    assert exported_blocks[-1] == {"type": "content", "content": "Compliance JSON marker"}
    assert "Compliance JSON marker" in exported_message["display_content"]
    assert message.content == raw_content


def test_prepare_chat_download_docx_uses_rendered_content():
    chat = Chats(
        id="chat-1",
        user_id="user-1",
        title="NVIDIA summary",
        project_id=None,
        share=None,
        archived=False,
        pinned_position=None,
        meta={},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    messages = [
        ChatMessages(
            id="msg-1",
            chat_id="chat-1",
            model_id="model-1",
            role="assistant",
            content=json.dumps(
                [
                    {"type": "reasoning", "content": "hidden thought", "meta": {"reasoningtime": 1.0}},
                    {"type": "content", "content": "Visible answer"},
                ]
            ),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]

    result = chat_download.prepare_chat_download("user-1", "chat-1", "docx", _FakeDb(chat=chat, messages=messages))

    with zipfile.ZipFile(BytesIO(result["content"])) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Visible answer" in document_xml
    assert "hidden thought" not in document_xml
    assert "reasoningtime" not in document_xml


def test_pdf_export_language_resolves_saved_user_language(monkeypatch):
    from app.users import init as users_init

    monkeypatch.setattr(users_init, "get_user_setting_value", lambda user_id, page, key, db: "de-DE")

    assert chat_download._resolve_pdf_export_language("user-1", object()) == "de"
    assert chat_download._normalize_pdf_export_language("unsupported") == "en"


def test_pdf_export_language_prefers_user_row_settings():
    user = User(
        id="user-1",
        email="user@example.com",
        group_id="default",
        hashed_password="x",
        first_name="Test",
        last_name="User",
        settings={"general": {"language": "es-MX"}},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert chat_download._resolve_pdf_export_language("user-1", _FakeDb(users=[user])) == "es"


def test_pdf_export_translations_exist_for_all_supported_languages():
    for language in chat_download.SUPPORTED_PDF_EXPORT_LANGUAGES:
        backend_missing = [key for key in chat_download.PDF_EXPORT_I18N_KEYS if key not in chat_download.PDF_EXPORT_TRANSLATIONS[language]]
        assert backend_missing == []

        path = REPO_ROOT / "frontend" / "i18n" / language / "index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in chat_download.PDF_EXPORT_I18N_KEYS if key not in payload]
        assert missing == []

    assert chat_download._pdf_export_t("de", "pdf_export_page", page=3) == "Seite 3"


@pytest.mark.parametrize("meta", [{"shadow_deleted": True}, {"status": "temp"}, '{"status": "temp"}'])
def test_full_chat_export_rejects_deleted_and_temporary_chats_by_default(meta):
    chat = Chats(id="chat-1", user_id="user-1", meta=meta)

    with pytest.raises(HTTPException) as exc_info:
        export_chat_full("user-1", "chat-1", _FakeDb(chat=chat))

    assert exc_info.value.status_code == 404
    exported = export_chat_full("user-1", "chat-1", _FakeDb(chat=chat), include_deleted_or_temp=True)
    assert exported["chat"]["id"] == "chat-1"


def test_user_chat_export_payload_excludes_deleted_and_temporary_chats_by_default(monkeypatch):
    normal_chat = Chats(id="chat-normal", user_id="user-1", meta={})
    shadow_deleted_chat = Chats(id="chat-deleted", user_id="user-1", meta={"shadow_deleted": True})
    temp_chat = Chats(id="chat-temp", user_id="user-1", meta={"status": "temp"})
    exported_calls = []

    def _fake_export_chat_full(user_id, chat_id, db, include_deleted_or_temp=False):
        exported_calls.append((chat_id, include_deleted_or_temp))
        return {"chat": {"id": chat_id}, "messages": []}

    monkeypatch.setattr(chat_io, "export_chat_full", _fake_export_chat_full)

    payload = chat_io.export_user_chats_payload(
        "user-1",
        _FakeDb(chats=[normal_chat, shadow_deleted_chat, temp_chat]),
    )
    assert [item["chat"]["id"] for item in payload["data"]["chats"]] == ["chat-normal"]
    assert exported_calls == [("chat-normal", False)]

    exported_calls.clear()
    payload = chat_io.export_user_chats_payload(
        "user-1",
        _FakeDb(chats=[normal_chat, shadow_deleted_chat, temp_chat]),
        include_deleted_or_temp=True,
    )
    assert [item["chat"]["id"] for item in payload["data"]["chats"]] == [
        "chat-normal",
        "chat-deleted",
        "chat-temp",
    ]
    assert exported_calls == [
        ("chat-normal", True),
        ("chat-deleted", True),
        ("chat-temp", True),
    ]


def test_user_chat_export_payload_applies_compliance_watermark(monkeypatch):
    monkeypatch.setattr(
        chat_compliance,
        "get_compliance_watermark",
        lambda *args, **kwargs: "Self export marker",
    )
    chat = Chats(id="chat-1", user_id="user-1", meta={})
    message = ChatMessages(
        id="msg-1",
        chat_id="chat-1",
        model_id="model-1",
        role="assistant",
        content=json.dumps([{"type": "content", "content": "Answer"}]),
    )

    payload = chat_io.export_user_chats_payload(
        "user-1",
        _FakeDb(chat=chat, messages=[message]),
    )
    exported_blocks = json.loads(payload["data"]["chats"][0]["messages"][0]["content"])

    assert exported_blocks[-1] == {"type": "content", "content": "Self export marker"}


def test_all_chat_import_does_not_trust_payload_user_id_as_local_owner(monkeypatch):
    victim = User(id="victim-user", email="victim@example.com")

    def fail_import_user_chats_payload(*args, **kwargs):
        raise AssertionError("all-chat import should not target a local user by payload user_id")

    monkeypatch.setattr(chat_io, "import_user_chats_payload", fail_import_user_chats_payload)

    result = chat_io.import_all_chats_payload(
        {
            "export_type": "chats",
            "export_version": chat_io.current_chat_export_version,
            "data": {
                "user_reference_map": {},
                "chats": [
                    {
                        "chat": {
                            "id": "source-chat",
                            "user_id": "victim-user",
                            "title": "Injected chat",
                        },
                        "messages": [],
                    }
                ],
                "count": 1,
            },
        },
        _FakeDb(users=[victim]),
    )

    assert result["created_count"] == 0
    assert result["created"] == []
    assert result["warnings"] == [
        {
            "index": 0,
            "source_user_id": "victim-user",
            "source_chat_id": "source-chat",
            "warning": "Import skipped because referenced user was not found",
        }
    ]


def test_all_chat_import_resolves_target_user_through_reference_map(monkeypatch):
    target = User(id="target-user", email="target@example.com")
    captured = {}

    def fake_import_user_chats_payload(user_id, payload, db):
        captured["user_id"] = user_id
        captured["payload"] = payload
        captured["db"] = db
        return {
            "created_count": 1,
            "created_message_count": 0,
            "created": [{"chat_id": "new-chat", "title": "Mapped chat", "message_count": 0}],
            "skipped_count": 0,
            "skipped": [],
            "errors": [],
        }

    monkeypatch.setattr(chat_io, "import_user_chats_payload", fake_import_user_chats_payload)
    db = _FakeDb(users=[target])

    result = chat_io.import_all_chats_payload(
        {
            "export_type": "chats",
            "export_version": chat_io.current_chat_export_version,
            "data": {
                "user_reference_map": {"source-user": "target@example.com"},
                "chats": [
                    {
                        "chat": {
                            "id": "source-chat",
                            "user_id": "source-user",
                            "title": "Mapped chat",
                        },
                        "messages": [],
                    }
                ],
                "count": 1,
            },
        },
        db,
    )

    assert captured["user_id"] == "target-user"
    assert captured["db"] is db
    assert captured["payload"]["data"]["chats"][0]["chat"]["user_id"] == "source-user"
    assert result["created"] == [
        {
            "user_id": "target-user",
            "chat_id": "new-chat",
            "title": "Mapped chat",
            "message_count": 0,
        }
    ]
    assert result["warnings"] == []


def test_user_chat_import_rejects_invalid_archive_role_and_pin_values():
    result = chat_io.import_user_chats_payload(
        "user-1",
        {
            "export_type": "chats",
            "export_version": chat_io.current_chat_export_version,
            "data": {
                "chats": [
                    {
                        "chat": {
                            "id": "source-chat",
                            "user_id": "source-user",
                            "title": "Broken chat",
                            "archived": "false",
                            "pinned_position": -1,
                        },
                        "messages": [
                            {
                                "id": "msg-1",
                                "model_id": "model-1",
                                "role": "moderator",
                                "content": "hello",
                            }
                        ],
                    }
                ],
                "count": 1,
            },
        },
        _FakeDb(),
    )

    assert result["created_count"] == 0
    assert result["errors"]
    assert "chat.archived" in result["errors"][0]["error"]
    assert "chat.pinned_position" in result["errors"][0]["error"]
    assert "messages.0.role" in result["errors"][0]["error"]


def test_user_chat_import_rejects_unversioned_single_chat_payload():
    """The pre-1.0 single-chat shape is no longer an accepted import format."""
    with pytest.raises(HTTPException) as exc_info:
        chat_io.import_user_chats_payload(
            "user-1",
            {"chat": {"id": "source-chat"}, "messages": []},
            _FakeDb(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported export_type for chat import."


def test_user_chat_import_rejects_wrapper_metadata_and_count_mismatches():
    with pytest.raises(HTTPException) as exc_info:
        chat_io.import_user_chats_payload(
            "user-1",
            {
                "export_type": "chats",
                "export_version": chat_io.current_chat_export_version,
                "data": {
                    "chats": [],
                    "count": 1,
                    "metadata": {"unexpected": True},
                },
            },
            _FakeDb(),
        )

    assert exc_info.value.status_code == 400
    assert "data.metadata" in exc_info.value.detail

    with pytest.raises(HTTPException) as count_exc_info:
        chat_io.import_user_chats_payload(
            "user-1",
            {
                "export_type": "chats",
                "export_version": chat_io.current_chat_export_version,
                "data": {
                    "chats": [],
                    "count": 1,
                },
            },
            _FakeDb(),
        )

    assert count_exc_info.value.status_code == 400
    assert "data.count" in count_exc_info.value.detail


def test_openwebui_import_request_requires_strict_force_archived_bool():
    from app.chats.schemas import OpenWebUIChatImportRequest

    with pytest.raises(ValidationError):
        OpenWebUIChatImportRequest.model_validate(
            {
                "chats": [{}],
                "force_archived": "false",
            }
        )


def test_openwebui_import_request_rejects_an_empty_export():
    from app.chats.schemas import OpenWebUIChatImportRequest

    with pytest.raises(ValidationError):
        OpenWebUIChatImportRequest.model_validate({"chats": []})
