import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _root_health_check_function() -> ast.FunctionDef:
    """Return the AST node for the root liveness handler without importing the app."""
    module = ast.parse((BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "root_health_check":
            return node
    raise AssertionError("root_health_check was not found")


def test_liveness_endpoints_include_server_version():
    """Ensure both public liveness probes expose the running server version."""
    handler = _root_health_check_function()
    route_paths = {
        decorator.args[0].value
        for decorator in handler.decorator_list
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        )
    }

    assert {"/health", "/healthz"}.issubset(route_paths)

    returned_dicts = [
        node.value
        for node in ast.walk(handler)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    ]
    assert returned_dicts, "root_health_check must return a JSON-serializable dict"

    returned_keys = {
        key.value
        for returned_dict in returned_dicts
        for key in returned_dict.keys
        if isinstance(key, ast.Constant)
    }
    returned_names = {
        value.id
        for returned_dict in returned_dicts
        for value in returned_dict.values
        if isinstance(value, ast.Name)
    }

    assert "version" in returned_keys
    assert "APP_VERSION" in returned_names
