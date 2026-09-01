import ast
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "app" / "llm" / "elevenlabs" / "schemas.py"


def _field_keys():
    module = ast.parse(SCHEMA_PATH.read_text())
    keys = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "FieldSchema":
            continue
        for keyword in node.keywords:
            if keyword.arg == "key" and isinstance(keyword.value, ast.Constant):
                keys.append(keyword.value.value)
    return keys


class ElevenLabsProviderSchemaTests:
    def test_enable_logging_is_persisted_as_provider_setting(self):
        field_keys = _field_keys()

        assert "settings.enable_logging" in field_keys
        assert "enable_logging" not in field_keys
