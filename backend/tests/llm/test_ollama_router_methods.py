import ast
from pathlib import Path


ROUTER_SOURCE = Path(__file__).resolve().parents[2] / "app" / "llm" / "ollama" / "router.py"


def _ollama_route_methods() -> dict[str, set[str]]:
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
    routes: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name):
                continue
            if decorator.func.value.id != "ollama_router":
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue

            path = decorator.args[0].value
            if not isinstance(path, str):
                continue
            routes.setdefault(path, set()).add(decorator.func.attr.upper())

    return routes


def test_ollama_mutating_routes_do_not_use_get():
    """Keep the remaining mutating Ollama operations on non-GET methods."""

    routes = _ollama_route_methods()
    expected_methods = {
        "/model/download": {"POST"},
        "/model": {"DELETE"},
        "/model/load": {"POST"},
        "/model/unload": {"POST"},
    }

    for path, methods in expected_methods.items():
        assert routes[path] == methods


def test_ollama_legacy_delete_get_route_is_not_registered():
    assert "/model/delete" not in _ollama_route_methods()
