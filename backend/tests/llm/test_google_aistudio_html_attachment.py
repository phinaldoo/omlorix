from app.files import utils as file_utils
from app.llm.google_aistudio import utils as google_utils


class _NoNativeUploadClient:
    """Fail if source-text HTML accidentally reaches Google's file service."""

    class files:
        @staticmethod
        def upload(*_args, **_kwargs):
            raise AssertionError("HTML source must not use provider-native upload")


def test_google_aistudio_inlines_html_as_text_for_text_only_model(tmp_path, monkeypatch):
    """Google models receive HTML markup as inert text conversation context."""
    html_path = tmp_path / "page.html"
    html_source = '<html><body><script>alert("inert")</script>VISIBLE_HTML</body></html>'
    html_path.write_text(html_source, encoding="utf-8")
    file_info = {
        "file_name": html_path.name,
        "file_size": html_path.stat().st_size,
        "file_type": "text/html; charset=utf-8",
        "file_category": "document",
        "path": str(html_path),
        "meta": {"original_filename": html_path.name},
    }
    monkeypatch.setattr(
        file_utils,
        "get_file_info",
        lambda _user_id, _file_id: file_info,
    )

    result = google_utils.upload_files(
        db=None,
        client=_NoNativeUploadClient(),
        file_ids=["html-1"],
        user_id="user-1",
        uploaded_cleanup=[],
        counters={"document": {"count": 0, "max": -1}},
        input_formats_allowed=["text"],
    )

    assert result["unsupported"] is False
    text_parts = [part.text for part in result["parts"] if part.text]
    assert any('"model_context_representation": "text_extract"' in text for text in text_parts)
    assert any(html_source in text for text in text_parts)
