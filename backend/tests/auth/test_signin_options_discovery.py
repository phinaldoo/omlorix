import ast
from pathlib import Path


AUTH_UTILS_PATH = Path(__file__).resolve().parents[2] / "app" / "auth" / "utils.py"


def _function_node(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            names.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def test_signin_options_exposes_server_capabilities_not_account_methods():
    source = AUTH_UTILS_PATH.read_text(encoding="utf-8")
    function = _function_node(source, "get_signin_options")
    return_node = next(
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    )

    top_level_keys = {
        key.value
        for key in return_node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }

    assert "server_methods" in top_level_keys
    assert "identifier_methods" in top_level_keys
    assert "methods" not in top_level_keys


def test_signin_options_does_not_lookup_account_readiness():
    source = AUTH_UTILS_PATH.read_text(encoding="utf-8")
    function = _function_node(source, "get_signin_options")

    call_names = _call_names(function)

    assert "get_user" not in call_names
    assert "list_user_passkeys" not in call_names
