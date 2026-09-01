"""Regression tests for the compact chat model-settings wire contract."""

from types import SimpleNamespace

from app.files.schemas import (
    supported_file_format_catalog,
    supported_file_format_groups_for_model_input_formats,
    supported_file_formats_for_model_input_formats,
)
from app.llm.router import _schema_to_compact_payload
from app.llm.model_schemas import get_parameter_basic_schema
from app.utils.schemas import FieldSchema, Option, Section, Sections


def test_compact_schema_omits_null_defaults_and_mcp_inventory():
    """Only meaningful control data should cross the chat API boundary."""
    schema = Sections(
        sections=[
            Section(
                title="Model Context",
                description="Decide which context the model should get.",
                fields=[
                    FieldSchema(
                        key="settings.enabled_mcp_servers",
                        label="Enabled MCP servers",
                        description="Choose the MCP servers available for the next request. Servers are disabled by default.",
                        type="select",
                        multiple=True,
                        required=False,
                        value=[],
                        options=[Option(value="private-server-id", label="Private server")],
                    ),
                    FieldSchema(
                        key="settings.store",
                        label="Store responses",
                        description="Store responses.",
                        type="boolean",
                        required=False,
                        default=False,
                        value=False,
                        dependency_value=False,
                    ),
                ],
            )
        ]
    )

    payload = _schema_to_compact_payload(schema)
    section = payload["sections"][0]
    mcp_field, boolean_field = section["fields"]

    assert "key" not in section
    assert "options" not in mcp_field
    assert "required" not in mcp_field
    assert "metadata" not in mcp_field
    assert "placeholder" not in mcp_field
    assert "title" not in section
    assert "description" not in section
    assert "label" not in mcp_field
    assert "description" not in mcp_field
    assert "private-server-id" not in str(payload)
    # False is meaningful for values/defaults/dependencies and must survive;
    # only false-valued presentation flags are disposable.
    assert boolean_field["default"] is False
    assert boolean_field["value"] is False
    assert boolean_field["dependency_value"] is False


def test_compact_file_groups_expand_to_legacy_mime_semantics():
    """The cached catalog must reproduce the former per-model MIME payload."""
    input_formats = ["text", "image", "pdf", "text_document"]
    groups = supported_file_format_groups_for_model_input_formats(input_formats)
    catalog = supported_file_format_catalog()
    expanded = {
        mime
        for group in groups
        for mime in catalog[group]
    }
    legacy = {
        mime
        for category in supported_file_formats_for_model_input_formats(input_formats)
        for mime in category["file_formats"]
    }

    assert groups == ["image", "document", "text_extracted_document"]
    assert expanded == legacy


def test_text_only_models_keep_text_extracted_attachment_support():
    """SVG/HTML text extraction remains available without native documents."""
    groups = supported_file_format_groups_for_model_input_formats(["text"])
    catalog = supported_file_format_catalog()
    legacy = supported_file_formats_for_model_input_formats(["text"])

    assert groups == ["text_extracted_document"]
    assert set(catalog[groups[0]]) == set(legacy[0]["file_formats"])


def test_system_instruction_schema_is_localizable_and_multiline(monkeypatch):
    """The conversation override must carry stable i18n keys and use a textarea."""

    from app.groups import models as group_models
    from app.users import models as user_models

    monkeypatch.setattr(
        user_models,
        "get_user",
        lambda _db, _user_id: SimpleNamespace(group_id="group-1"),
    )
    monkeypatch.setattr(
        group_models,
        "get_group",
        lambda _db, _group_id: SimpleNamespace(settings={}),
    )

    schema = get_parameter_basic_schema(SimpleNamespace(), "user-1")
    field = next(
        candidate
        for candidate in schema.sections[0].fields
        if candidate.key == "system_instruction"
    )

    assert field.type == "string"
    assert field.input_type == "textarea"
    assert field.i18n_label == "model_settings_system_instruction_label"
    assert field.i18n_description == "model_settings_system_instruction_description"
    assert field.i18n_placeholder == "model_settings_system_instruction_placeholder"

    compact_field = next(
        candidate
        for candidate in _schema_to_compact_payload(schema)["sections"][0]["fields"]
        if candidate["key"] == "system_instruction"
    )
    assert compact_field["input_type"] == "textarea"
    assert compact_field["i18n_label"] == field.i18n_label
    assert compact_field["i18n_description"] == field.i18n_description
    assert compact_field["i18n_placeholder"] == field.i18n_placeholder
    assert "label" not in compact_field
    assert "description" not in compact_field
    assert "placeholder" not in compact_field
