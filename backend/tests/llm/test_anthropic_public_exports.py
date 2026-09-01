"""Regression coverage for Anthropic's direct public exports."""

from app.llm.anthropic import attachments, generation, messages, models, utils


def test_anthropic_utils_exports_focused_implementations_directly() -> None:
    """The public module must not wrap functions to inject hidden dependencies."""
    expected_exports = {
        "_anthropic_capability_supported": models._anthropic_capability_supported,
        "_anthropic_model_value": models._anthropic_model_value,
        "_assert_anthropic_model_listing_allowed": (
            models._assert_anthropic_model_listing_allowed
        ),
        "_serialize_anthropic_model": models._serialize_anthropic_model,
        "_uses_anthropic_base_models_api": models._uses_anthropic_base_models_api,
        "anthropic_title_generation": generation.anthropic_title_generation,
        "create_anthropic_model": models.create_anthropic_model,
        "create_anthropic_provider": models.create_anthropic_provider,
        "get_anthropic_client": models.get_anthropic_client,
        "list_anthropic_models": models.list_anthropic_models,
        "reformat_chat_history": messages.reformat_chat_history,
        "upload_files": attachments.upload_files,
    }

    for export_name, implementation in expected_exports.items():
        assert getattr(utils, export_name) is implementation
