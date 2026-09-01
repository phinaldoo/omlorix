"""Keep provider APIs free from test-only dependency injection parameters."""

import ast
from pathlib import Path


LLM_ROOT = Path(__file__).resolve().parents[2] / "app" / "llm"


def _default_parameters(function: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield parameters that have positional or keyword-only defaults."""
    positional = function.args.posonlyargs + function.args.args
    if function.args.defaults:
        positional = positional[-len(function.args.defaults) :]
        yield from zip(positional, function.args.defaults, strict=True)

    yield from (
        (parameter, default)
        for parameter, default in zip(
            function.args.kwonlyargs,
            function.args.kw_defaults,
            strict=True,
        )
        if default is not None
    )


def test_provider_functions_do_not_expose_private_dependency_defaults() -> None:
    """Tests patch implementation modules instead of changing production APIs."""
    violations: list[str] = []

    for path in sorted(LLM_ROOT.rglob("*.py")):
        # Router dependencies such as ``_user=Depends(...)`` are request wiring,
        # not injectable implementation functions.
        if path.name == "router.py":
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for parameter, default in _default_parameters(node):
                if parameter.arg.startswith("_"):
                    relative_path = path.relative_to(LLM_ROOT.parent.parent)
                    violations.append(
                        f"{relative_path}:{node.lineno} {node.name}"
                        f"({parameter.arg}={ast.unparse(default)})"
                    )

    assert violations == []
