"""OpenRouter generation-parameter schema construction.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.openrouter import schemas as _compat_source

_COMPAT_DEPENDENCIES = {
    "get_openrouter_parameters_schema": (
        "FieldSchema",
        "Option",
        "Section",
        "Sections",
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
    "_set_schema_field_value",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_get_openrouter_parameters_schema(
    model_settings: dict | None = None,
) -> Sections:
    schema = Sections(
        sections=[
            Section(
                title="Generation parameters",
                description="Fine-tune OpenRouter sampling behavior and advanced options.",
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
                        description="Restrict sampling to the top K most likely tokens.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.frequency_penalty",
                        label="Frequency penalty",
                        description="Reduce repetition by penalizing frequently used tokens.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.presence_penalty",
                        label="Presence penalty",
                        description="Encourage exploration of new topics by penalizing previous tokens.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.repetition_penalty",
                        label="Repetition penalty",
                        description="Diversify generations by penalizing repeated tokens.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.min_p",
                        label="Min P",
                        description="Minimum probability threshold for token sampling.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.top_a",
                        label="Top A",
                        description="Alternate sampling parameter supported by select models.",
                        type="string",
                        input_type="float",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.seed",
                        label="Seed",
                        description="Deterministic seed for reproducible generations.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.max_tokens",
                        label="Max tokens",
                        description="Maximum number of tokens generated in the response.",
                        type="string",
                        input_type="int",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.stop",
                        label="Stop sequences",
                        description="Sequences that will terminate generation when encountered.",
                        type="string",
                        input_type="list[str]",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.logit_bias",
                        label="Logit bias",
                        description="Bias specific tokens by providing a token->bias mapping.",
                        type="string",
                        input_type="dict[str,float]",
                        required=False,
                    ),
                    FieldSchema(
                        key="settings.verbosity",
                        label="Verbosity",
                        description="Controls diagnostic verbosity for supported models.",
                        type="select",
                        options=[
                            Option(
                                value="low",
                                label="low",
                                i18n_label="llm.shared.settings.verbosity.option.low",
                            ),
                            Option(
                                value="medium",
                                label="medium",
                                i18n_label="llm.shared.settings.verbosity.option.medium",
                            ),
                            Option(
                                value="high",
                                label="high",
                                i18n_label="llm.shared.settings.verbosity.option.high",
                            ),
                        ],
                        required=False,
                    ),
                ],
            )
        ]
    )

    def _maybe_set(field_key: str):
        if not model_settings:
            return
        key = field_key.split(".")[-1]
        value = model_settings.get(key)
        if value is None:
            return
        if isinstance(value, str) and value == "":
            return
        if isinstance(value, (list, dict)) and not value:
            return
        _set_schema_field_value(schema, field_key, value)

    for field in (
        "settings.temperature",
        "settings.top_p",
        "settings.top_k",
        "settings.frequency_penalty",
        "settings.presence_penalty",
        "settings.repetition_penalty",
        "settings.min_p",
        "settings.top_a",
        "settings.seed",
        "settings.max_tokens",
        "settings.stop",
        "settings.logit_bias",
        "settings.verbosity",
    ):
        _maybe_set(field)

    return schema
