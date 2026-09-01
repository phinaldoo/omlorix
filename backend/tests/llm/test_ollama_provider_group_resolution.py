import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

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

    class _FakeMarkItDown:
        def __init__(self, *args, **kwargs):
            pass

    fake_markitdown.MarkItDown = _FakeMarkItDown
    sys.modules["markitdown"] = fake_markitdown

if "app.chats.models" not in sys.modules:
    fake_chat_models = ModuleType("app.chats.models")
    fake_chat_models.Chats = type("Chats", (), {})
    fake_chat_models.ChatMessages = type("ChatMessages", (), {})
    fake_chat_models.create_chat_message = lambda *args, **kwargs: None
    fake_chat_models.get_chat = lambda *args, **kwargs: None
    fake_chat_models.update_chat_title = lambda *args, **kwargs: None
    fake_chat_models.get_chat_messages = lambda *args, **kwargs: []
    sys.modules["app.chats.models"] = fake_chat_models

from app.llm.ollama import schemas as ollama_schemas
from app.llm.ollama import utils as ollama_utils
from app.llm.ollama.schemas import InputFormatEnum, OllamaModelSettings
from app.llm.ollama.utils import ollama_chat
from app.llm.openrouter import utils as openrouter_utils


def test_idless_ollama_tool_calls_receive_distinct_fallback_ids():
    """Each ID-less provider call needs one stable identifier of its own."""
    fallback_ids = [
        ollama_utils._resolve_ollama_tool_call_id(None),
        ollama_utils._resolve_ollama_tool_call_id(None),
    ]

    assert all(tool_call_id.startswith("call_") for tool_call_id in fallback_ids)
    assert len(set(fallback_ids)) == 2
    assert ollama_utils._resolve_ollama_tool_call_id("provider-call-1") == "provider-call-1"


def test_ollama_effective_input_formats_exclude_native_audio_and_video():
    assert [item.value for item in InputFormatEnum] == [
        "text",
        "image",
        "pdf",
        "text_document",
    ]
    assert OllamaModelSettings.model_fields["max_document_count"].default == -1
    assert ollama_utils._resolve_ollama_input_formats(
        None,
        ["completion", "vision"],
    ) == ["text", "image", "pdf", "text_document"]
    assert ollama_utils._resolve_ollama_input_formats(
        ["text", "audio", "video", "pdf"],
        [],
    ) == ["text", "pdf"]


def test_ollama_model_schema_exposes_effective_document_controls():
    with patch.object(
        ollama_schemas,
        "get_ollama_model_info",
        return_value={
            "capabilities": ["completion", "vision"],
            "details": {},
        },
    ):
        schema = ollama_schemas.get_ollama_model_schema(
            MagicMock(),
            "provider-1",
            model_name="llava",
        )

    fields = {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }
    assert fields["settings.input_formats"].value == [
        "text",
        "image",
        "pdf",
        "text_document",
    ]
    assert "settings.max_document_count" in fields
    assert "settings.max_audio_count" not in fields
    assert "settings.max_video_count" not in fields


def test_ollama_model_creation_persists_only_effective_attachment_capabilities():
    db = MagicMock()
    with patch(
        "app.llm.ollama.utils.list_models_ollama",
        return_value=[{"id": "llava"}],
    ), patch(
        "app.llm.ollama.utils.get_model_capabilities",
        return_value=["completion", "vision", "audio", "video", "tools"],
    ):
        ollama_utils.ollama_create_model(
            "provider-1",
            "llava",
            "LLaVA",
            "Vision model",
            "ollama",
            {"input_formats": ["text", "image", "pdf", "text_document"]},
            ["web_search"],
            {"everyone": True},
            "active",
            db,
        )

    saved_model = db.add.call_args.args[0]
    assert saved_model.capabilities == [
        "completion",
        "vision",
        "tools",
        "documents",
    ]


class OllamaProviderGroupResolutionTests:
    def test_preflight_checks_resolved_provider_instead_of_group_id(self):
        db = MagicMock()
        db_model = SimpleNamespace(
            id="model-1",
            provider_id="group-1",
            model_name="llama3.2",
            settings={"max_image_count": 2, "max_document_count": 3},
            capabilities=[],
        )
        resolved_provider = SimpleNamespace(id="provider-2")
        checked_provider_ids: list[str] = []

        def fake_get_ollama_client(*args, **kwargs):
            return object(), resolved_provider

        def fake_check_ollama_version(db_arg, ollama_provider_id=None, **kwargs):
            checked_provider_ids.append(ollama_provider_id)
            raise HTTPException(status_code=400, detail="preflight stop")

        with patch("app.llm.ollama.utils.get_ollama_client", side_effect=fake_get_ollama_client), patch(
            "app.llm.ollama.utils.reformat_chat_history",
            return_value={"formatted": [], "unsupported_file_ids": []},
        ) as reformat_mock, patch(
            "app.llm.ollama.utils.get_default_system_instruction",
            return_value="system",
        ), patch(
            "app.llm.ollama.utils.append_system_instruction_sections",
            side_effect=lambda instruction, sections: instruction,
        ), patch(
            "app.llm.ollama.utils.check_ollama_version",
            side_effect=fake_check_ollama_version,
        ):
            event = next(
                ollama_chat(
                    "chat-1",
                    [],
                    db,
                    db_model=db_model,
                    user_id="user-1",
                    user_role="admin",
                )
            )

        payload = json.loads(event)
        assert checked_provider_ids == ["provider-2"]
        assert reformat_mock.call_args.kwargs["max_image_count"] == 2
        assert reformat_mock.call_args.kwargs["max_document_count"] == 3
        assert payload == {"t": "e", "d": "preflight stop"}


class OllamaAttachmentPolicyTests:
    def test_reformat_chat_history_blocks_images_when_input_formats_are_text_only(self, tmp_path):
        image_path = tmp_path / "secret.png"
        image_path.write_bytes(b"SECRET_IMAGE_BYTES_DO_NOT_SEND")

        def fake_get_file_info(_user_id, file_id):
            return {
                "id": file_id,
                "file_name": "secret.png",
                "file_category": "image",
                "file_type": "image/png",
                "path": str(image_path),
            }

        with patch("app.llm.ollama.utils.get_file_info", side_effect=fake_get_file_info):
            result = ollama_utils.reformat_chat_history(
                [{"role": "user", "content": "summarize", "images": ["image-1"]}],
                user_id="user-1",
                input_formats_allowed=["text"],
                use_group_context=False,
                use_project_context=False,
            )

        assert result["unsupported"] is True
        assert result["unsupported_file_ids"] == ["image-1"]
        assert "images" not in result["formatted"][0]
        assert "SECRET_IMAGE_BYTES_DO_NOT_SEND" not in json.dumps(result["formatted"])

    def test_text_only_models_extract_svg_from_messages_and_legacy_projects(self, tmp_path):
        svg_path = tmp_path / "diagram.svg"
        svg_path.write_text('<svg><text>VISIBLE_VECTOR_LABEL</text></svg>', encoding="utf-8")
        html_path = tmp_path / "page.html"
        html_path.write_text('<html><body>VISIBLE_HTML_SOURCE</body></html>', encoding="utf-8")
        text_path = tmp_path / "private.txt"
        text_path.write_text("NON_SVG_DOCUMENT_MUST_STAY_BLOCKED", encoding="utf-8")

        def fake_get_file_info(_user_id, file_id):
            if file_id == "message-html":
                return {
                    "id": file_id,
                    "file_name": "page.html",
                    "file_category": "document",
                    "file_type": "text/html; charset=utf-8",
                    "path": str(html_path),
                }
            if file_id == "plain-document":
                return {
                    "id": file_id,
                    "file_name": "private.txt",
                    "file_category": "document",
                    "file_type": "text/plain",
                    "path": str(text_path),
                }
            return {
                "id": file_id,
                "file_name": f"{file_id}.svg",
                "file_category": "document",
                "file_type": "image/svg+xml; charset=utf-8",
                "path": str(svg_path),
            }

        legacy_project_svg = SimpleNamespace(
            id="project-svg",
            file_category="image",
            file_type="image/svg+xml; charset=utf-8",
        )
        with patch("app.llm.ollama.utils.get_project_context_start", return_value=""), patch(
            "app.llm.ollama.utils.get_project_context_end",
            return_value="",
        ), patch(
            "app.llm.ollama.utils.safe_list_project_files",
            return_value=[legacy_project_svg],
        ), patch("app.llm.ollama.utils.get_file_info", side_effect=fake_get_file_info):
            result = ollama_utils.reformat_chat_history(
                [{
                    "role": "user",
                    "content": "inspect attachments",
                    "documents": ["message-svg", "message-html", "plain-document"],
                }],
                user_id="user-1",
                db=MagicMock(),
                project_id="project-1",
                input_formats_allowed=["text"],
                use_group_context=False,
                use_project_context=True,
            )

        formatted = json.dumps(result["formatted"])
        assert "VISIBLE_VECTOR_LABEL" in formatted
        assert "VISIBLE_HTML_SOURCE" in formatted
        assert "NON_SVG_DOCUMENT_MUST_STAY_BLOCKED" not in formatted
        assert result["unsupported"] is True
        assert result["unsupported_file_ids"] == ["plain-document"]
    def test_openrouter_text_only_models_extract_svg_from_messages_and_legacy_projects(self, tmp_path):
        svg_path = tmp_path / "diagram.svg"
        svg_path.write_text('<svg><text>VISIBLE_VECTOR_LABEL</text></svg>', encoding="utf-8")
        html_path = tmp_path / "page.html"
        html_path.write_text('<html><body>VISIBLE_HTML_SOURCE</body></html>', encoding="utf-8")
        text_path = tmp_path / "private.txt"
        text_path.write_text("NON_SVG_DOCUMENT_MUST_STAY_BLOCKED", encoding="utf-8")

        def fake_get_file_info(_user_id, file_id):
            if file_id == "message-html":
                return {
                    "id": file_id,
                    "file_name": "page.html",
                    "file_category": "document",
                    "file_type": "text/html; charset=utf-8",
                    "path": str(html_path),
                }
            if file_id == "plain-document":
                return {
                    "id": file_id,
                    "file_name": "private.txt",
                    "file_category": "document",
                    "file_type": "text/plain",
                    "path": str(text_path),
                }
            return {
                "id": file_id,
                "file_name": f"{file_id}.svg",
                "file_category": "document",
                "file_type": "image/svg+xml; charset=utf-8",
                "path": str(svg_path),
            }

        legacy_project_svg = SimpleNamespace(
            id="project-svg",
            file_category="image",
            file_type="image/svg+xml; charset=utf-8",
        )
        with patch("app.llm.openrouter.utils.get_project_context_start", return_value=""), patch(
            "app.llm.openrouter.utils.get_project_context_end",
            return_value="",
        ), patch(
            "app.llm.openrouter.utils.safe_list_project_files",
            return_value=[legacy_project_svg],
        ), patch("app.llm.openrouter.utils.get_file_info", side_effect=fake_get_file_info):
            result = openrouter_utils.reformat_chat_history(
                [{
                    "role": "user",
                    "content": "inspect attachments",
                    "documents": ["message-svg", "message-html", "plain-document"],
                }],
                user_id="user-1",
                db=MagicMock(),
                project_id="project-1",
                input_formats_allowed=["text"],
                use_group_context=False,
                use_project_context=True,
            )

        formatted = json.dumps(result["formatted"])
        assert "VISIBLE_VECTOR_LABEL" in formatted
        assert "VISIBLE_HTML_SOURCE" in formatted
        assert "NON_SVG_DOCUMENT_MUST_STAY_BLOCKED" not in formatted
        assert result["unsupported"] is True
        assert result["unsupported_file_ids"] == ["plain-document"]

    def test_reference_parts_attach_to_latest_user_prompt(self):
        result = ollama_utils.reformat_chat_history(
            [
                {"role": "user", "content": "first prompt"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "latest prompt"},
            ],
            user_id=None,
            db=None,
            reference_parts=["selected canvas text"],
            chat_reference_context="Referenced chat transcript",
            use_group_context=False,
            use_project_context=False,
        )

        first_user = result["formatted"][0]
        latest_user = result["formatted"][-1]
        assert first_user["content"] == "first prompt"
        assert "selected canvas text" not in first_user["content"]
        assert latest_user["role"] == "user"
        assert latest_user["content"].endswith("latest prompt")
        assert latest_user["content"].index("selected canvas text") < latest_user["content"].index("latest prompt")
        assert "selected canvas text" in latest_user["content"]
        assert "Referenced chat transcript" in latest_user["content"]

    def test_group_and_project_context_images_respect_text_only_input_formats(self, tmp_path):
        image_path = tmp_path / "secret.png"
        image_path.write_bytes(b"SECRET_IMAGE_BYTES_DO_NOT_SEND")

        def fake_get_file_info(_user_id, file_id):
            return {
                "id": file_id,
                "file_name": f"{file_id}.png",
                "file_category": "image",
                "file_type": "image/png",
                "path": str(image_path),
            }

        project_file = SimpleNamespace(
            id="project-image-1",
            file_category="image",
            file_type="image/png",
        )

        with patch("app.llm.ollama.utils.get_user_group_setting_value", return_value=True), patch(
            "app.llm.ollama.utils.get_group_context_start",
            return_value={"context": "group context", "group_context_file_ids": ["group-image-1"]},
        ), patch("app.llm.ollama.utils.get_group_context_end", return_value="group end"), patch(
            "app.llm.ollama.utils.get_project_context_start",
            return_value="project context",
        ), patch("app.llm.ollama.utils.get_project_context_end", return_value="project end"), patch(
            "app.llm.ollama.utils.safe_list_project_files",
            return_value=[project_file],
        ), patch("app.llm.ollama.utils.get_file_info", side_effect=fake_get_file_info):
            result = ollama_utils.reformat_chat_history(
                [],
                user_id="user-1",
                db=MagicMock(),
                project_id="project-1",
                input_formats_allowed=["text"],
                use_group_context=True,
                use_project_context=True,
            )

        assert result["unsupported"] is True
        assert result["unsupported_file_ids"] == ["group-image-1", "project-image-1"]
        assert not any("images" in message for message in result["formatted"])
        assert "SECRET_IMAGE_BYTES_DO_NOT_SEND" not in json.dumps(result["formatted"])
