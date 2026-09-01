import ast
from pathlib import Path


ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "auth" / "router.py"


def test_signin_route_enforces_same_origin_before_signin():
    tree = ast.parse(ROUTER_PATH.read_text(encoding="utf-8"))
    signin_route = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "signin_route"
    )

    top_level_calls = []
    for statement in signin_route.body:
        value = statement.value if isinstance(statement, (ast.Expr, ast.Return)) else None
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            top_level_calls.append(value.func.id)

    assert top_level_calls[:2] == ["enforce_same_origin", "signin"]
