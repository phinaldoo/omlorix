"""OpenAI generation-settings schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openai import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_parameters_schema_filled": (
        "FieldSchema",
        "Option",
        "Section",
        "Sections",
        "_get_openai_model_caps",
        "_object_to_dict",
        "_set_schema_field_value",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "FieldSchema",
    "Option",
    "Section",
    "Sections",
    "_get_openai_model_caps",
    "_object_to_dict",
    "_set_schema_field_value",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_parameters_schema_filled(
    model_settings: dict | None = None,
    model_name: str | None = None,
    openai_provider_type: str | None = None,
):
    """Get filled parameters schema."""
    # Get supported service tiers from model capabilities
    model_caps = (
        _get_openai_model_caps(
            model_name,
            openai_provider_type=openai_provider_type,
        )
        if model_name
        else None
    )
    supported_tiers = (
        model_caps.get("supported_service_tier", ["flex", "standard", "priority"])
        if model_caps
        else ["flex", "standard", "priority"]
    )

    # Build priority processing options based on supported tiers
    priority_options = [
        Option(value=tier, label=tier)
        for tier in ["flex", "standard", "priority"]
        if tier in supported_tiers
    ]

    generation_schema = Sections(
        sections=[
            Section(
                title="Generation parameters",
                description="Fine-tune sampling behavior and maximum output sizes for this model.",
                fields=[
                    FieldSchema(
                        key="settings.temperature",
                        label="Temperature",
                        description="Controls randomness for text generation.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.top_p",
                        label="Top P",
                        description="Limits nucleus sampling to a percentage of probability mass.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.frequency_penalty",
                        label="Frequency penalty",
                        description="Reduces repetition by penalizing frequent tokens.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.presence_penalty",
                        label="Presence penalty",
                        description="Encourages the model to talk about new topics.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.max_output_tokens",
                        label="Max output tokens",
                        description="Maximum number of tokens allowed in the generated output.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.store",
                        label="Store responses",
                        description="Store response data for at least 30 days.",
                        type="boolean",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.send_user_identifier",
                        label="Share user identifier with OpenAI",
                        description="When enabled, Omlorix sends the current user's ID as OpenAI's safety_identifier to help monitor abuse. See https://platform.openai.com/docs/guides/safety-checks for details.",
                        type="boolean",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.priority_processing",
                        label="Priority processing",
                        description="Select the desired processing priority tier.",
                        type="select",
                        options=priority_options,
                        default="standard"
                        if "standard" in supported_tiers
                        else (supported_tiers[0] if supported_tiers else "standard"),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.verbosity",
                        label="Verbosity",
                        description="Controls response length when supported by the model.",
                        type="select",
                        options=[],
                        default=None,
                        required=False,
                    ),
                ],
            ),
        ]
    )
    image_schema = Sections(
        sections=[
            Section(
                title="Image inputs",
                description="Control how OpenAI processes uploaded image inputs.",
                fields=[
                    FieldSchema(
                        key="settings.image_detail",
                        label="Image quality",
                        description="Detail level OpenAI should use for image understanding. Leave empty to use the provider default.",
                        type="select",
                        options=[
                            Option(
                                value="auto",
                                label="Auto",
                                i18n_label="llm.shared.settings.image_detail.option.auto",
                            ),
                            Option(
                                value="low",
                                label="Low",
                                i18n_label="llm.shared.settings.image_detail.option.low",
                            ),
                            Option(
                                value="high",
                                label="High",
                                i18n_label="llm.shared.settings.image_detail.option.high",
                            ),
                            Option(
                                value="original",
                                label="Original",
                                i18n_label="llm.shared.settings.image_detail.option.original",
                            ),
                        ],
                        required=False,
                    ),
                ],
            ),
        ]
    )
    schema = Sections(
        sections=(generation_schema.sections or []) + (image_schema.sections or [])
    )

    settings_dict = _object_to_dict(model_settings)
    if not settings_dict:
        return schema

    def _set_if_present(key: str):
        value = settings_dict.get(key)
        if value is None:
            return
        _set_schema_field_value(schema, f"settings.{key}", value)

    for field_key in (
        "temperature",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "max_output_tokens",
        "store",
        "send_user_identifier",
        "verbosity",
        "tool_search",
        "image_detail",
    ):
        _set_if_present(field_key)

    return schema
