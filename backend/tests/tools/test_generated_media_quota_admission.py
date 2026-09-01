from __future__ import annotations

import pytest

from app.files.utils import (
    FileQuotaError,
    USER_FILE_COUNT_QUOTA_REACHED,
)
from app.tools import helper as tool_helper
from app.tools.audio_generation import utils as audio_utils
from app.tools.errors import SafeToolExecutionError
from app.tools.image_generation import utils as image_utils
from app.tools.music_generation import utils as music_utils
from app.tools.slide_presentation.rendering import utils as slide_rendering_utils
from app.tools.video_generation import utils as video_utils


class _DB:
    def close(self) -> None:
        """Match the generated-tool session cleanup contract."""


def _quota_denial(*_args, **_kwargs):
    raise FileQuotaError(
        code=USER_FILE_COUNT_QUOTA_REACHED,
        message="Maximum number of uploaded files reached",
    )


@pytest.mark.parametrize(
    ("module", "invoke"),
    [
        (image_utils, lambda: image_utils.image_generation("draw", "user-1")),
        (video_utils, lambda: video_utils.video_generation("animate", "user-1")),
        (audio_utils, lambda: audio_utils.audio_generation("speak", "user-1")),
        (
            music_utils,
            lambda: music_utils.music_generation(description="song", user_id="user-1"),
        ),
    ],
)
def test_media_generation_rejects_quota_before_provider_work(monkeypatch, module, invoke):
    """Every cited media tool reserves capacity before resolving/calling a provider."""

    monkeypatch.setattr(module, "SessionLocal", _DB)
    monkeypatch.setattr(module, "reserve_user_file_quota", _quota_denial)
    monkeypatch.setattr(
        module,
        "release_user_file_quota_reservation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        module,
        "_resolve_provider",
        lambda *_args, **_kwargs: pytest.fail("provider resolution must not run"),
    )
    if module is image_utils:
        monkeypatch.setattr(
            image_utils,
            "_get_image_generation_config",
            lambda: {
                "provider_id": "provider-1",
                "model_name": "model-1",
                "settings": {},
            },
        )

    with pytest.raises(FileQuotaError) as exc_info:
        invoke()

    assert exc_info.value.code == USER_FILE_COUNT_QUOTA_REACHED


def test_slide_renderer_rejects_quota_before_remote_renderer_work(monkeypatch, tmp_path):
    """PPTX count admission happens before any renderer HTTP call."""

    monkeypatch.setattr(slide_rendering_utils, "reserve_user_file_quota", _quota_denial)
    monkeypatch.setattr(
        slide_rendering_utils,
        "release_user_file_quota_reservation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        slide_rendering_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "renderer-1",
                "base_url": "https://renderer.example",
                "api_key": "",
            }
        ],
    )
    monkeypatch.setattr(
        slide_rendering_utils,
        "_build_input_files_payload",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        slide_rendering_utils,
        "assert_url_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        slide_rendering_utils.httpx,
        "Client",
        lambda *_args, **_kwargs: pytest.fail("renderer HTTP must not run"),
    )

    with pytest.raises(FileQuotaError) as exc_info:
        slide_rendering_utils.render_slide_presentation(
            "<html><body><section>Slide</section></body></html>",
            "user-1",
            "deck.pptx",
            presentation_dir=tmp_path,
            db=_DB(),
        )

    assert exc_info.value.code == USER_FILE_COUNT_QUOTA_REACHED


def test_tool_error_transport_preserves_the_file_quota_code():
    """Model tools receive the same stable code as HTTP upload responses."""

    def denied():
        return _quota_denial()

    with pytest.raises(SafeToolExecutionError) as exc_info:
        tool_helper._call_quota_aware_file_tool(denied)

    assert exc_info.value.code == USER_FILE_COUNT_QUOTA_REACHED
    assert exc_info.value.safe_message == "Maximum number of uploaded files reached"
    assert exc_info.value.allow_same_response_retry is False
