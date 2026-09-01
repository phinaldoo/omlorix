"""Ollama generation-settings schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.ollama import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_parameters_schema_filled": (
        "FieldAttributes",
        "FieldSchema",
        "Section",
        "Sections",
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
    "FieldAttributes",
    "FieldSchema",
    "Section",
    "Sections",
    "_object_to_dict",
    "_set_schema_field_value",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_parameters_schema_filled(model_settings):
    """Get parameters schema filled."""
    settings_dict = _object_to_dict(model_settings)

    schema = Sections(
        sections=[
            Section(
                title="Generation parameters",
                description="Fine tune the model's behavior",
                fields=[
                    FieldSchema(
                        key="settings.num_keep",
                        label="Number to keep",
                        description="Optional number of tokens to keep.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.seed",
                        label="Seed",
                        description="If specified, repeated requests with the same seed and parameters should return the same result (determinism may vary per model).",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.num_predict",
                        label="Num predict",
                        description="Optional number of tokens to predict.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.top_k",
                        label="Top K",
                        description="This limits the model’s choice of tokens at each step, making it choose from a smaller set. A value of 1 means the model always picks the most likely next token, leading to predictable results. By default this setting is disabled, letting the model consider all choices.",
                        type="string",
                        input_type="int",
                        attributes=FieldAttributes(min=0),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.top_p",
                        label="Top P",
                        description="Limits the model’s choices to the most likely tokens whose probabilities add up to P (dynamic Top-K). Lower values make responses more predictable; higher values allow more diverse tokens.",
                        type="string",
                        input_type="float",
                        attributes=FieldAttributes(min=0, max=1),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.min_p",
                        label="Min P",
                        description="Represents the minimum probability for a token to be considered relative to the most likely token (e.g., 0.1 keeps tokens that are at least 1/10th as probable as the best option).",
                        type="string",
                        input_type="float",
                        attributes=FieldAttributes(min=0, max=1),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.typical_p",
                        label="Typical P",
                        description="Typical sampling rate supported by some models.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.repeat_last_n",
                        label="Repeat last N",
                        description="Optional repeat_last_n.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.temperature",
                        label="Temperature",
                        description="This setting influences the variety in the model’s responses. Lower values lead to more predictable and typical responses, while higher values encourage more diverse and less common responses. At 0, the model always gives the same response for a given input. Valid range: 0.0 to 2.0.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.repeat_penalty",
                        label="Repeat penalty",
                        description="Optional repeat_penalty.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.presence_penalty",
                        label="Presence penalty",
                        description="Adjusts how often the model repeats specific tokens already used in the input. Higher values reduce repetition, while negative values encourage reuse.",
                        type="string",
                        input_type="float",
                        attributes=FieldAttributes(min=-2, max=2),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.frequency_penalty",
                        label="Frequency penalty",
                        description="Controls repetition based on how often tokens appear in the input. Higher values decrease reuse of frequent tokens; negative values encourage reuse.",
                        type="string",
                        input_type="float",
                        attributes=FieldAttributes(min=-2, max=2),
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.penalize_newline",
                        label="Penalize newline",
                        description="Apply repeat penalty to newline tokens.",
                        type="boolean",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.stop",
                        label="Stop sequences",
                        description="Stop generation immediately if any token from this list is emitted.",
                        type="string",
                        input_type="list[str]",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.numa",
                        label="NUMA",
                        description="Enable NUMA-aware execution for compatible systems.",
                        type="boolean",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.num_ctx",
                        label="Context length",
                        description="Number of context tokens available to the model.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.num_batch",
                        label="Batch size",
                        description="Number of tokens to process per evaluation batch.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.num_gpu",
                        label="GPU count",
                        description="Number of GPU layers to offload when running the model.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.main_gpu",
                        label="Main GPU",
                        description="Index of the primary GPU to use for inference.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.use_mmap",
                        label="Use mmap",
                        description="Enable memory-mapped weights loading.",
                        type="boolean",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.num_thread",
                        label="Thread count",
                        description="Number of CPU threads to use for inference.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.keep_alive",
                        label="Keep alive",
                        description="Seconds the model remains loaded in memory after use.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                ],
            )
        ]
    )
    if not settings_dict:
        return schema

    def _set_if_present(settings_key: str, field_key: str | None = None):
        value = settings_dict.get(settings_key)
        if value is None:
            return
        _set_schema_field_value(schema, field_key or f"settings.{settings_key}", value)

    _set_if_present("num_keep")
    _set_if_present("seed")
    _set_if_present("num_predict")
    _set_if_present("top_k")
    _set_if_present("top_p")
    _set_if_present("min_p")
    _set_if_present("typical_p")
    _set_if_present("repeat_last_n")
    _set_if_present("temperature")
    _set_if_present("repeat_penalty")
    _set_if_present("presence_penalty")
    _set_if_present("frequency_penalty")
    _set_if_present("penalize_newline")
    _set_if_present("stop")
    _set_if_present("numa")
    _set_if_present("num_ctx")
    _set_if_present("num_batch")
    _set_if_present("num_gpu")
    _set_if_present("main_gpu")
    _set_if_present("use_mmap")
    _set_if_present("num_thread")
    _set_if_present("keep_alive")

    return schema
