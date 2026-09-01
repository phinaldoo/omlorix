"""Google AI Studio generation-settings schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.google_aistudio import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_parameters_schema_filled": (
        "FieldAttributes",
        "FieldSchema",
        "Option",
        "Section",
        "Sections",
        "_set_schema_field_value",
        "get_aistudio_safety_schema_filled",
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
    "FieldAttributes",
    "FieldSchema",
    "Option",
    "Section",
    "Sections",
    "_set_schema_field_value",
    "get_aistudio_safety_schema_filled",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_parameters_schema_filled(model_settings: dict | None = None):
    generation_schema = Sections(
        sections=[
            Section(
                title="Generation parameters",
                description="Fine-tune sampling behavior, seeds, and stop conditions for Gemini responses.",
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
                        key="settings.top_k",
                        label="Top K",
                        description="Limits sampling to the top K most likely tokens.",
                        type="string",
                        input_type="int",
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
                        key="settings.stop_sequences",
                        label="Stop sequences",
                        description="List of sequences that will stop generation when produced.",
                        type="string",
                        input_type="list[str]",
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
                        key="settings.frequency_penalty",
                        label="Frequency penalty",
                        description="Reduces repetition by penalizing frequent tokens.",
                        type="string",
                        input_type="float",
                        attributes=FieldAttributes(min=-2, max=2),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.seed",
                        label="Seed",
                        description="Deterministic seed for reproducible outputs.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                ],
            ),
        ]
    )
    video_schema = Sections(
        sections=[
            Section(
                title="Video settings",
                description="Control how Gemini samples video frames for uploaded videos and native YouTube video inputs.",
                fields=[
                    FieldSchema(
                        key="settings.video_fps",
                        label="Video FPS",
                        description="Custom video frame sampling rate. Leave empty to use Gemini's default sampling and omit it from the request.",
                        type="string",
                        input_type="float",
                        required=False,
                        placeholder="E.g. 0.5, 1, 5",
                    ),
                    FieldSchema(
                        key="settings.media_resolution",
                        label="Video resolution",
                        description="Global Gemini media resolution for video frames. Leave empty to omit it from the request and use Gemini's default.",
                        type="select",
                        options=[
                            Option(
                                value="low",
                                label="Low",
                                i18n_label="llm.shared.settings.media_resolution.option.low",
                            ),
                            Option(
                                value="medium",
                                label="Medium",
                                i18n_label="llm.shared.settings.media_resolution.option.medium",
                            ),
                            Option(
                                value="high",
                                label="High",
                                i18n_label="llm.shared.settings.media_resolution.option.high",
                            ),
                        ],
                        required=False,
                    ),
                ],
            ),
        ]
    )
    value_temperature = None
    value_top_p = None
    value_top_k = None
    value_max_output_tokens = None
    value_stop_sequences = None
    value_presence_penalty = None
    value_frequency_penalty = None
    value_seed = None
    value_video_fps = None
    value_media_resolution = None
    value_safety_harassment = None
    value_safety_hate_speech = None
    value_safety_sexually_explicit = None
    value_safety_dangerous_content = None
    value_safety_civic_integrity = None
    if model_settings:
        value_temperature = model_settings.get("temperature")
        value_top_p = model_settings.get("top_p")
        value_top_k = model_settings.get("top_k")
        value_max_output_tokens = model_settings.get("max_output_tokens")
        value_stop_sequences = model_settings.get("stop_sequences")
        value_presence_penalty = model_settings.get("presence_penalty")
        value_frequency_penalty = model_settings.get("frequency_penalty")
        value_seed = model_settings.get("seed")
        value_video_fps = model_settings.get("video_fps")
        value_media_resolution = model_settings.get("media_resolution")
        value_safety_harassment = model_settings.get("safety_harassment")
        value_safety_hate_speech = model_settings.get("safety_hate_speech")
        value_safety_sexually_explicit = model_settings.get("safety_sexually_explicit")
        value_safety_dangerous_content = model_settings.get("safety_dangerous_content")
        value_safety_civic_integrity = model_settings.get("safety_civic_integrity")
    # Temperature
    if value_temperature:
        _set_schema_field_value(
            generation_schema, "settings.temperature", value_temperature
        )
    # Top P
    if value_top_p:
        _set_schema_field_value(generation_schema, "settings.top_p", value_top_p)

    # Top K
    if value_top_k:
        _set_schema_field_value(generation_schema, "settings.top_k", value_top_k)

    # Max Output Tokens
    if value_max_output_tokens:
        _set_schema_field_value(
            generation_schema, "settings.max_output_tokens", value_max_output_tokens
        )

    # Stop Sequences
    if value_stop_sequences:
        _set_schema_field_value(
            generation_schema, "settings.stop_sequences", value_stop_sequences
        )

    # Presence Penalty
    if value_presence_penalty:
        _set_schema_field_value(
            generation_schema, "settings.presence_penalty", value_presence_penalty
        )

    # Frequency Penalty
    if value_frequency_penalty:
        _set_schema_field_value(
            generation_schema, "settings.frequency_penalty", value_frequency_penalty
        )

    # Seed
    if value_seed:
        _set_schema_field_value(generation_schema, "settings.seed", value_seed)

    # Video FPS
    if value_video_fps is not None:
        _set_schema_field_value(video_schema, "settings.video_fps", value_video_fps)

    # Video Resolution
    if value_media_resolution:
        _set_schema_field_value(
            video_schema, "settings.media_resolution", value_media_resolution
        )

    safety_schema = get_aistudio_safety_schema_filled(model_settings)
    for field_key, field_value in (
        ("settings.safety_harassment", value_safety_harassment),
        ("settings.safety_hate_speech", value_safety_hate_speech),
        ("settings.safety_sexually_explicit", value_safety_sexually_explicit),
        ("settings.safety_dangerous_content", value_safety_dangerous_content),
        ("settings.safety_civic_integrity", value_safety_civic_integrity),
    ):
        if field_value is not None:
            _set_schema_field_value(safety_schema, field_key, field_value)

    return Sections(
        sections=(generation_schema.sections or [])
        + (video_schema.sections or [])
        + (safety_schema.sections or [])
    )
