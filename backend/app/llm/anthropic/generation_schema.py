"""Schema construction for Anthropic generation parameters."""

from app.utils.schemas import FieldSchema, Section, Sections, _set_schema_field_value


def get_parameters_schema_filled(model_settings: dict | None = None):
    """Get filled parameters schema."""
    schema = Sections(
        sections=[
            Section(
                title="Generation parameters",
                fields=[
                    FieldSchema(
                        key="settings.max_tokens",
                        label="Max tokens",
                        description="Optional ceiling on the response tokens for reasoning modes.",
                        type="string",
                        input_type="int",
                        required=True,
                    ),
                    FieldSchema(
                        key="settings.stop_sequences",
                        label="Stop sequences",
                        description="Sequences that will terminate generation when encountered.",
                        type="string",
                        input_type="list[str]",
                        required=False,
                    ),
                ],
            )
        ],
    )
    value_max_tokens = None
    value_stop_sequences = None
    if model_settings:
        value_stop_sequences = model_settings.get("stop_sequences")
        value_max_tokens = model_settings.get("max_tokens")
    if value_max_tokens:
        _set_schema_field_value(schema, "settings.max_tokens", value_max_tokens)
    if value_stop_sequences:
        _set_schema_field_value(schema, "settings.stop_sequences", value_stop_sequences)
    return schema
