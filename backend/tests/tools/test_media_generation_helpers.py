from types import SimpleNamespace

from app.tools.audio_generation import utils as audio_utils
from app.tools.music_generation import utils as music_utils
from app.tools.video_generation import utils as video_utils


def test_audio_safe_original_name_strips_paths_and_normalizes_extension():
    assert audio_utils._safe_original_name("../unsafe/final.wav", "mp3") == "final.mp3"
    assert audio_utils._safe_original_name("voice", "opus") == "voice.opus"
    assert audio_utils._safe_original_name("", "") == "generated_audio.mp3"


def test_audio_guess_file_type_prefers_audio_mime_only():
    assert audio_utils._guess_file_type("clip.wav", "text/plain") == "audio/x-wav"
    assert audio_utils._guess_file_type("clip.unknown", "audio/ogg") == "audio/ogg"
    assert audio_utils._guess_file_type("clip.unknown", "text/plain") == "audio/mpeg"


def test_audio_config_merge_ignores_provider_and_model_overrides():
    merged = audio_utils._merge_audio_generation_config(
        {"provider_id": "admin-provider", "model_name": "admin-model", "voice": "alloy", "response_format": "mp3"},
        {"provider_id": "attacker-provider", "model_name": "attacker-model", "voice": "nova", "response_format": "wav"},
    )

    assert merged["provider_id"] == "admin-provider"
    assert merged["model_name"] == "admin-model"
    assert merged["voice"] == "nova"
    assert merged["response_format"] == "wav"


def test_video_coerce_optional_bool_handles_common_wire_values():
    assert video_utils._coerce_optional_bool(None, default=True) is True
    assert video_utils._coerce_optional_bool(" yes ") is True
    assert video_utils._coerce_optional_bool("off", default=True) is False
    assert video_utils._coerce_optional_bool(0, default=True) is False
    assert video_utils._coerce_optional_bool("surprise", default=True) is True


def test_video_extract_attachment_ids_accepts_json_strings_dicts_and_lists():
    assert video_utils._extract_attachment_ids('["a", {"file_id": "b"}, ["c"]]') == ["a", "b", "c"]
    assert video_utils._extract_attachment_ids({"id": "file-1"}) == ["file-1"]
    assert video_utils._extract_attachment_ids(" file-2 ") == ["file-2"]
    assert video_utils._extract_attachment_ids("") == []


def test_video_collects_reference_ids_from_content_and_history():
    content = {
        "images": [{"id": "image-1"}],
        "videos": '["video-1"]',
        "documents": {"file_id": "doc-1"},
    }
    history = [
        {"content": content, "images": ["image-2"]},
        SimpleNamespace(content='{"audios": [{"id": "audio-1"}]}', documents=[{"file_id": "doc-2"}]),
    ]

    assert video_utils._collect_file_ids_from_content(content) == ["image-1", "video-1", "doc-1"]
    assert video_utils._collect_file_ids_from_chat_history(history) == [
        "image-2",
        "image-1",
        "video-1",
        "doc-1",
        "doc-2",
        "audio-1",
    ]


def test_video_resolve_reference_file_ids_prioritizes_explicit_then_history_and_dedupes():
    resolved = video_utils._resolve_reference_file_ids(
        db=object(),
        chat_id=None,
        chat_history=[{"images": ["history-1", "shared"], "content": {"images": ["history-2"]}}],
        explicit_file_ids=["explicit-1", "shared"],
    )

    assert resolved == ["explicit-1", "shared", "history-1", "history-2"]


def test_music_safe_original_name_and_file_type_helpers():
    assert music_utils._safe_original_name("../unsafe/song.wav", "mp3") == "song.mp3"
    assert music_utils._safe_original_name("song", "wav") == "song.wav"
    assert music_utils._guess_file_type("song.wav", "text/plain") == "audio/x-wav"
    assert music_utils._guess_file_type("song.bin", "audio/mpeg") == "audio/mpeg"


def test_music_reference_image_ids_support_message_objects_and_deduping():
    history = [
        {"images": [{"id": "image-1"}, {"file_id": "image-2"}], "content": [{"images": ["image-3"]}]},
        SimpleNamespace(images=["image-2", "image-4"], content=[]),
    ]

    assert music_utils._collect_reference_image_ids(history) == ["image-1", "image-2", "image-3", "image-4"]
