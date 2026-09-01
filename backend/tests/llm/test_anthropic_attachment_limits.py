import builtins
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.anthropic import attachments as anthropic_attachments
from app.llm.anthropic import messages as anthropic_messages
from app.llm.anthropic import utils as anthropic_utils


def _file_info(path, *, file_id="file-1", file_size=10, file_type="image/png", file_category="image"):
    return {
        "id": file_id,
        "file_name": f"{file_id}.png",
        "path": str(path),
        "file_size": file_size,
        "file_type": file_type,
        "file_category": file_category,
        "meta": {"original_filename": f"{file_id}.png"},
    }


def test_anthropic_upload_files_skips_oversized_images_before_reading(tmp_path, monkeypatch):
    image_path = tmp_path / "oversized.png"
    image_path.write_bytes(b"not actually large")

    monkeypatch.setattr(
        anthropic_attachments,
        "get_file_info",
        lambda user_id, file_id: _file_info(
            image_path,
            file_id=file_id,
            file_size=anthropic_utils.ANTHROPIC_FILE_UPLOAD_LIMIT_BYTES + 1,
        ),
    )

    def fail_if_opened(*args, **kwargs):
        raise AssertionError("oversized Anthropic attachments must not be opened")

    monkeypatch.setattr(builtins, "open", fail_if_opened)

    result = anthropic_utils.upload_files(
        db=None,
        file_ids=["oversized"],
        user_id="user-1",
        input_formats_allowed=["image"],
    )

    assert result["unsupported"] is True
    assert result["unsupported_file_ids"] == ["oversized"]
    assert not [part for part in result["parts"] if part.get("type") == "image"]


def test_anthropic_reformat_chat_history_enforces_image_count_before_reading(tmp_path, monkeypatch):
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    file_infos = {
        "first": _file_info(first_path, file_id="first"),
        "second": _file_info(second_path, file_id="second"),
    }
    opened_paths = []

    monkeypatch.setattr(
        anthropic_attachments,
        "get_file_info",
        lambda user_id, file_id: file_infos.get(file_id),
    )
    monkeypatch.setattr(
        anthropic_messages,
        "get_user_group_setting_value",
        lambda *args, **kwargs: False,
    )
    real_open = builtins.open

    def track_open(path, *args, **kwargs):
        opened_paths.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", track_open)

    result = anthropic_utils.reformat_chat_history(
        [{"role": "user", "content": "hello", "images": ["first", "second"]}],
        user_id="user-1",
        db=None,
        max_image_count=1,
        max_document_count=0,
        input_formats_allowed=["image"],
        use_group_context=False,
        use_project_context=False,
    )

    image_parts = [
        part
        for message in result["formatted"]
        for part in message["content"]
        if part.get("type") == "image"
    ]
    assert len(image_parts) == 1
    assert opened_paths == [str(first_path)]
    assert result["unsupported"] is True
    assert result["unsupported_file_ids"] == ["second"]


def test_anthropic_reference_parts_attach_to_latest_user_prompt(monkeypatch):
    monkeypatch.setattr(
        anthropic_messages,
        "get_user_group_setting_value",
        lambda *args, **kwargs: False,
    )

    result = anthropic_utils.reformat_chat_history(
        [
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "latest prompt"},
        ],
        user_id="user-1",
        db=None,
        reference_parts=["selected canvas text"],
        chat_reference_context="Referenced chat transcript",
        use_group_context=False,
        use_project_context=False,
    )

    first_user_texts = [
        part.get("text", "")
        for part in result["formatted"][0]["content"]
        if isinstance(part, dict)
    ]
    latest_user_texts = [
        part.get("text", "")
        for part in result["formatted"][-1]["content"]
        if isinstance(part, dict)
    ]

    assert result["formatted"][0]["role"] == "user"
    assert result["formatted"][-1]["role"] == "user"
    assert "first prompt" in first_user_texts
    assert not any("selected canvas text" in text for text in first_user_texts)
    assert "latest prompt" in latest_user_texts
    assert latest_user_texts.index("latest prompt") > 0
    assert any("selected canvas text" in text for text in latest_user_texts)
    assert any("Referenced chat transcript" in text for text in latest_user_texts)
