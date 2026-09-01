"""Focused compatibility tests for OpenRouter's dedicated video API."""

from app.llm.openrouter import video_generation


def test_openrouter_video_fallback_schema_includes_current_documented_presets():
    """Fallback controls must retain presets advertised by OpenRouter's API."""
    schema = video_generation.get_video_generation_schema_part_2("test/video-model")
    fields = {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }

    assert "768p" in [option.value for option in fields["resolution"].options]
    aspect_ratios = [option.value for option in fields["aspect_ratio"].options]
    assert "3:2" in aspect_ratios
    assert "2:3" in aspect_ratios
