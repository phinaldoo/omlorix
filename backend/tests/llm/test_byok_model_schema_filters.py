import ast
from pathlib import Path

from app.utils.schemas import Option


ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "llm" / "router.py"


def _router_ast():
    return ast.parse(ROUTER_PATH.read_text())


def _literal_set_assignment(module_ast, name):
    for node in module_ast.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found")


def test_byok_model_schema_excludes_title_generation_inventory_fields():
    excluded_fields = _literal_set_assignment(
        _router_ast(),
        "BYOK_MODEL_SCHEMA_EXCLUDED_FIELDS",
    )

    assert {
        "settings.title_generation",
        "settings.title_generation_model",
        "settings.title_generation_model_id",
        "settings.custom_title_generation_instruction",
        "settings.allow_custom_generation_parameter",
        "status",
    }.issubset(excluded_fields)


def test_byok_model_schema_route_uses_shared_excluded_field_set():
    module_ast = _router_ast()
    route = next(
        node
        for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_byok_model_schema_route"
    )

    remove_calls = [
        node
        for node in ast.walk(route)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_remove_schema_fields"
    ]

    assert remove_calls, "BYOK model schema route should filter schema fields"
    assert any(
        len(call.args) >= 2
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "BYOK_MODEL_SCHEMA_EXCLUDED_FIELDS"
        for call in remove_calls
    )


def test_dynamic_model_options_receive_stable_translation_keys():
    expected_keys = {
        "text": "llm.shared.option.text",
        "image": "llm.shared.option.image",
        "pdf": "llm.shared.option.pdf",
        "text_document": "llm.shared.option.text_document",
        "none": "llm.shared.option.none",
        "concise": "llm.shared.option.concise",
        "detailed": "llm.shared.option.detailed",
        "auto": "llm.shared.settings.image_detail.option.auto",
        "flex": "llm.shared.option.flex",
        "standard": "llm.shared.settings.quality.option.standard",
        "priority": "llm.shared.option.priority",
        "low": "llm.shared.settings.verbosity.option.low",
        "medium": "llm.shared.settings.verbosity.option.medium",
        "high": "llm.shared.settings.verbosity.option.high",
    }

    for label, expected_key in expected_keys.items():
        assert Option(value=label, label=label).i18n_label == expected_key
