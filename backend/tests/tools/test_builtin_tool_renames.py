from app.tools.registry import (
    get_rate_limit_tool_description_i18n_key,
    get_rate_limit_tool_label_i18n_key,
    list_rate_limit_tool_keys,
    normalize_rate_limit_tool_key,
)
from app.tools.code_execution.utils import build_code_execution_tool_schema
from app.tools.schemas import tool_schemas
from app.tools.utils import get_tool_schemas, list_available_tool_options, resolve_enabled_tools
from app.admin.settings.schema_categories.code_execution import (
    CodeExecutionSettings,
    code_execution_schema,
)
from app.llm.system_instruction.chat import code_execution_tool


def test_builtin_tool_schemas_use_short_public_names():
    assert "weather" in tool_schemas
    assert "quiz" not in tool_schemas
    assert "flashcards" not in tool_schemas
    assert "stocks" not in tool_schemas
    assert "get_weather" not in tool_schemas
    assert "get_stock" not in tool_schemas
    assert "create_quiz" not in tool_schemas
    assert "create_flashcards" not in tool_schemas

    schema_names = {schema["name"] for schema in get_tool_schemas(["weather", "stocks", "quiz", "flashcards"])}
    assert schema_names == {"weather", "create_visualization"}


def test_legacy_builtin_tool_names_normalize_to_short_names():
    resolved = resolve_enabled_tools(["get_weather", "create_quiz", "create_flashcards"])

    assert resolved["tool_list"] == ["weather", "create_visualization"]
    assert [schema["name"] for schema in resolved["tool_schemas"]] == ["weather", "create_visualization"]


def test_retired_stock_tool_names_are_ignored():
    resolved = resolve_enabled_tools(["stocks", "get_stock"])

    assert resolved["tool_list"] == []
    assert resolved["tool_schemas"] == []


def test_legacy_latex_tool_setting_resolves_to_canvas():
    """Stored model settings migrate without re-exposing the retired tool."""
    resolved = resolve_enabled_tools(["latex_pdf"])

    assert resolved["tool_list"] == ["canvas"]
    assert [schema["name"] for schema in resolved["tool_schemas"]] == ["canvas"]


def test_rate_limit_registry_uses_short_builtin_tool_keys():
    tool_keys = list_rate_limit_tool_keys()

    assert "weather" in tool_keys
    assert "flashcards" not in tool_keys
    assert "quiz" not in tool_keys
    assert "stocks" not in tool_keys
    assert "get_weather" not in tool_keys
    assert "get_stock" not in tool_keys
    assert "create_flashcards" not in tool_keys
    assert "create_quiz" not in tool_keys
    assert normalize_rate_limit_tool_key("get_weather") == "weather"
    assert normalize_rate_limit_tool_key("get_stock") == "get_stock"
    assert normalize_rate_limit_tool_key("create_flashcards") == "create_visualization"
    assert normalize_rate_limit_tool_key("create_quiz") == "create_visualization"


def test_builtin_tool_options_include_admin_translation_keys():
    options = {item["name"]: item for item in list_available_tool_options(db=None)}

    expected_label_keys = {
        "weather": "rate_limit_tool_label_weather",
        "create_visualization": "rate_limit_tool_label_create_visualization",
        "subagent": "rate_limit_tool_label_subagent",
    }

    for tool_name, label_key in expected_label_keys.items():
        assert options[tool_name]["i18n_label"] == label_key
    assert "stocks" not in options
    assert "latex_pdf" not in options


def test_rate_limit_tool_i18n_keys_are_explicit_for_builtins_only():
    assert get_rate_limit_tool_label_i18n_key("get_weather") == "rate_limit_tool_label_weather"
    assert get_rate_limit_tool_description_i18n_key("weather") == "rate_limit_tool_description_weather"
    assert get_rate_limit_tool_label_i18n_key("custom_python_tool") is None
    assert get_rate_limit_tool_description_i18n_key("custom_python_tool") is None


def test_code_execution_schema_never_exposes_network_argument():
    schema = build_code_execution_tool_schema()

    assert "enable_network" not in schema["parameters"]["properties"]


def test_code_execution_admin_settings_have_no_network_override():
    field_keys = {
        field.key
        for section in code_execution_schema.sections
        for field in section.fields
    }

    assert "enable_network" not in CodeExecutionSettings.model_fields
    assert "enable_network" not in field_keys
    assert "default_timeout" not in CodeExecutionSettings.model_fields
    assert "default_timeout" not in field_keys


def test_code_execution_schema_only_exposes_pip_packages_when_supported():
    unsupported_schema = build_code_execution_tool_schema(allow_pip_packages=False)
    supported_schema = build_code_execution_tool_schema(allow_pip_packages=True)

    assert "pip_packages" not in unsupported_schema["parameters"]["properties"]
    assert "cannot be installed" in unsupported_schema["description"]
    assert "pip_packages" in supported_schema["parameters"]["properties"]
    assert "timeout" not in unsupported_schema["parameters"]["properties"]
    assert "timeout" not in supported_schema["parameters"]["properties"]


def test_code_execution_file_ids_description_requires_exact_file_ids():
    schema = build_code_execution_tool_schema()
    description = schema["parameters"]["properties"]["file_ids"]["description"]

    assert "exact file_id value only" in description
    assert "not the visible file name" in description


def test_code_execution_guides_models_to_reuse_and_edit_source_files():
    schema = build_code_execution_tool_schema()
    schema_description = schema["description"]
    code_description = schema["parameters"]["properties"]["code"]["description"]

    assert "first create a source file" in schema_description
    assert "smallest targeted edit" in schema_description
    assert "short, disposable commands or calculations" in schema_description
    assert "persistent source file" in code_description

    assert "first create a source file" in code_execution_tool
    assert "Reuse that same source file" in code_execution_tool
    assert "smallest targeted edit" in code_execution_tool
    assert "Do not overwrite or resend the complete source file" in code_execution_tool
    assert "execution environment was reset" in code_execution_tool


def test_code_execution_tool_schema_ignores_legacy_network_setting(monkeypatch):
    monkeypatch.setattr(
        "app.tools.code_execution.utils.get_settings_page_data",
        lambda _db, _page: {
            "enable_network": True,
            "max_output_length": 10000,
        },
    )
    schema = get_tool_schemas(["code_execution"], db=object())[0]

    assert "enable_network" not in schema["parameters"]["properties"]


def test_code_execution_tool_schema_uses_connection_pip_capability(monkeypatch):
    monkeypatch.setattr(
        "app.tools.code_execution.utils.get_settings_page_data",
        lambda _db, _page: {
            "max_output_length": 10000,
        },
    )
    monkeypatch.setattr(
        "app.tools.utils.code_execution_supports_external_pip_packages",
        lambda _db: False,
    )
    unsupported_schema = get_tool_schemas(["code_execution"], db=object())[0]

    monkeypatch.setattr(
        "app.tools.utils.code_execution_supports_external_pip_packages",
        lambda _db: True,
    )
    supported_schema = get_tool_schemas(["code_execution"], db=object())[0]

    assert "pip_packages" not in unsupported_schema["parameters"]["properties"]
    assert "pip_packages" in supported_schema["parameters"]["properties"]


def test_all_saved_study_selections_collapse_to_visualization():
    from app.tools.registry import RETIRED_STUDY_TOOL_ALIASES
    from app.tools.helper import AVAILABLE_TOOLS

    resolved = resolve_enabled_tools([*RETIRED_STUDY_TOOL_ALIASES, "create_visualization"])
    assert resolved["tool_list"] == ["create_visualization"]
    assert [schema["name"] for schema in resolved["tool_schemas"]] == ["create_visualization"]
    assert not set(RETIRED_STUDY_TOOL_ALIASES).intersection(AVAILABLE_TOOLS)
    assert not set(RETIRED_STUDY_TOOL_ALIASES).intersection(item["name"] for item in list_available_tool_options(db=None))


def test_saved_study_rate_limits_still_apply_to_replacement():
    from types import SimpleNamespace
    from app.llm.models import _tool_rate_limit_applies_to_user, _validate_rate_limit_tool_keys
    from app.llm.router import _schema_to_payload

    assert _validate_rate_limit_tool_keys(None, ["quiz", "create_flashcards", "create_visualization"]) == ["create_visualization"]
    policy = SimpleNamespace(target_type="tool", tool_keys=["quiz"], user_ids=["owner"], group_ids=[])
    assert _tool_rate_limit_applies_to_user(policy, "owner", None, "create_visualization")
    assert not _tool_rate_limit_applies_to_user(policy, "owner", None, "weather")
    original = {"sections": [{"fields": [{"key": "tools", "value": ["quiz", "flashcards"]}]}]}
    assert _schema_to_payload(original)["sections"][0]["fields"][0]["value"] == ["create_visualization"]
    assert original["sections"][0]["fields"][0]["value"] == ["quiz", "flashcards"]


def test_saved_byok_study_allowlist_enables_visualization(monkeypatch):
    monkeypatch.setattr("app.tools.utils.get_user_group_setting_value", lambda *args: ["quiz", "create_flashcards"])
    monkeypatch.setattr("app.tools.utils.list_available_tool_names", lambda **kwargs: ["create_visualization"])
    resolved = resolve_enabled_tools(["flashcards", "create_visualization"], db=object(), user_id="owner", byok={"provider": "openai"})
    assert resolved["tool_list"] == ["create_visualization"]
