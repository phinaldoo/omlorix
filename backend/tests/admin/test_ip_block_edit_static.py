import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _source_for_function(relative_path: str, function_name: str) -> str:
    """Return source for a named function so static route checks stay focused."""
    source = (REPO_ROOT / relative_path).read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{function_name} not found in {relative_path}")


def test_ip_block_edit_route_updates_saved_entry_without_recreating_it():
    router_source = (REPO_ROOT / "backend/app/ip_analytics/router.py").read_text()
    route_source = _source_for_function(
        "backend/app/ip_analytics/router.py",
        "edit_ip_block_route",
    )
    persistence_source = _source_for_function(
        "backend/app/ip_analytics/models.py",
        "update_blocked_ip",
    )

    assert (
        '@admin_router.put("/ip-address/blocked/{ip_address}", response_model=OperationResult)'
        in router_source
    )
    assert "payload: EditIPBlock" in route_source
    assert "existing_target is not None" in route_source
    assert "update_blocked_ip(" in route_source
    assert 'return OperationResult(status="success")' in route_source
    assert "entry.ip_address = ip_address" in persistence_source
    assert "entry.expires_at = expires_at" in persistence_source
    assert "entry.reason = reason" in persistence_source
    assert "entry.blocked_at =" not in persistence_source
