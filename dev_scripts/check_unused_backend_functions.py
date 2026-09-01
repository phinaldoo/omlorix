#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    ".venv-copilot",
    "node_modules",
    "venv",
    "dist",
    "build",
    "outputs",
    "backups",
    "alembic_main",
    "alembic_audit",
    "tests",
}

# FastAPI route decorator methods to exclude
_ROUTE_METHODS = frozenset({
    "get", "post", "put", "delete", "patch", "head", "options",
    "trace", "websocket", "api_route", "add_api_route",
})

# Pydantic validator decorator names to exclude
_PYDANTIC_VALIDATOR_DECORATORS = frozenset({
    "field_validator", "validator", "model_validator", "field_serializer",
    "model_serializer", "computed_field", "validate_call",
})

# ABC/abstract method decorators to exclude
_ABC_DECORATORS = frozenset({
    "abstractmethod", "abstractstaticmethod", "abstractclassmethod",
})

# Property decorators to exclude (accessed as attributes, not called)
_PROPERTY_DECORATORS = frozenset({
    "property", "cached_property",
})


@dataclass(frozen=True)
class FunctionDefInfo:
    module: str
    qualname: str
    file_path: Path
    lineno: int

    @property
    def key(self) -> str:
        return f"{self.module}:{self.qualname}"


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _module_name_for_file(backend_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(backend_root)
    if rel.name == "__init__.py":
        rel = rel.parent
    module = ".".join(rel.with_suffix("").parts)
    return module


def _is_fastapi_route_decorator(decorator: ast.expr) -> bool:
    """Check if decorator is a FastAPI route like @router.get() or @app.post()."""
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.attr in _ROUTE_METHODS
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            return func.attr in _ROUTE_METHODS
    return False


def _is_pydantic_validator_decorator(decorator: ast.expr) -> bool:
    """Check if decorator is a Pydantic validator like @field_validator, @validator, etc."""
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if isinstance(func, ast.Name):
            return func.id in _PYDANTIC_VALIDATOR_DECORATORS
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.attr in _PYDANTIC_VALIDATOR_DECORATORS
    elif isinstance(decorator, ast.Name):
        return decorator.id in _PYDANTIC_VALIDATOR_DECORATORS
    return False


def _has_route_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has any FastAPI route decorators."""
    return any(_is_fastapi_route_decorator(d) for d in node.decorator_list)


def _has_pydantic_validator_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has any Pydantic validator decorators."""
    return any(_is_pydantic_validator_decorator(d) for d in node.decorator_list)


def _is_abc_decorator(decorator: ast.expr) -> bool:
    """Check if decorator is an ABC abstract method decorator."""
    if isinstance(decorator, ast.Name):
        return decorator.id in _ABC_DECORATORS
    if isinstance(decorator, ast.Attribute) and isinstance(decorator.value, ast.Name):
        return decorator.attr in _ABC_DECORATORS
    return False


def _has_abc_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has any ABC abstract method decorators."""
    return any(_is_abc_decorator(d) for d in node.decorator_list)


def _is_property_decorator(decorator: ast.expr) -> bool:
    """Check if decorator is a property decorator."""
    if isinstance(decorator, ast.Name):
        return decorator.id in _PROPERTY_DECORATORS
    return False


def _has_property_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function has any property decorators."""
    return any(_is_property_decorator(d) for d in node.decorator_list)


class _DefCollector(ast.NodeVisitor):
    def __init__(self, module: str, file_path: Path) -> None:
        self._module = module
        self._file_path = file_path
        self._scope: list[str] = []
        self.functions: list[FunctionDefInfo] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if _has_route_decorator(node) or _has_pydantic_validator_decorator(node) or _has_abc_decorator(node) or _has_property_decorator(node):
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()
            return
        qual = ".".join([*self._scope, node.name]) if self._scope else node.name
        self.functions.append(
            FunctionDefInfo(
                module=self._module,
                qualname=qual,
                file_path=self._file_path,
                lineno=getattr(node, "lineno", 0) or 0,
            )
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if _has_route_decorator(node) or _has_pydantic_validator_decorator(node) or _has_abc_decorator(node) or _has_property_decorator(node):
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()
            return
        qual = ".".join([*self._scope, node.name]) if self._scope else node.name
        self.functions.append(
            FunctionDefInfo(
                module=self._module,
                qualname=qual,
                file_path=self._file_path,
                lineno=getattr(node, "lineno", 0) or 0,
            )
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()


class _RefCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.called_names: set[str] = set()
        self.called_attrs: set[tuple[str, str]] = set()
        # Track method names called on self/cls/super - these should match class methods
        self.called_method_names: set[str] = set()
        # Track names used as call arguments (e.g., callback references like _run_with_guard(func))
        self.callback_refs: set[str] = set()
        self.import_from: list[tuple[str, str]] = []
        self.import_module_as: list[tuple[str, str]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not node.module:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            # Track alias mapping: asname -> (module, original_name)
            # If no alias, asname is the original name
            asname = alias.asname or alias.name
            self.import_from.append((node.module, alias.name, asname))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            asname = alias.asname or alias.name
            self.import_module_as.append((asname, alias.name))

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        if isinstance(fn, ast.Name):
            self.called_names.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            base = fn.value
            if isinstance(base, ast.Name):
                self.called_attrs.add((base.id, fn.attr))
                # Track self.method(), cls.method() patterns
                if base.id in ("self", "cls"):
                    self.called_method_names.add(fn.attr)
                # Track method calls on ANY variable - catches polymorphic dispatch
                # like provider.is_enabled(), user.get_name(), etc.
                self.called_method_names.add(fn.attr)
            elif isinstance(base, ast.Call):
                # Track super().method() pattern
                if isinstance(base.func, ast.Name) and base.func.id == "super":
                    self.called_method_names.add(fn.attr)
                # Track method calls on function results like factory().method()
                self.called_method_names.add(fn.attr)
            else:
                # Track method calls on complex expressions like obj.attr.method()
                self.called_method_names.add(fn.attr)
        # Track function references passed as arguments (callbacks)
        for arg in node.args:
            if isinstance(arg, ast.Name):
                self.callback_refs.add(arg.id)
        for kw in node.keywords:
            if kw.arg and isinstance(kw.value, ast.Name):
                self.callback_refs.add(kw.value.id)
        self.generic_visit(node)


def _parse_file(path: Path) -> ast.AST | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError:
        return None


def _is_backend_module(module: str) -> bool:
    # Backend modules are either "app" or start with "app."
    # (The "backend" directory prefix is stripped in _module_name_for_file)
    return module == "app" or module.startswith("app.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend-root",
        default="backend",
    )
    parser.add_argument(
        "--output",
        default="temp/unused_backend_functions.txt",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    backend_root = (repo_root / args.backend_root).resolve()
    if not backend_root.exists() or not backend_root.is_dir():
        print(f"backend root not found: {backend_root}", file=sys.stderr)
        return 2

    py_files = _iter_python_files(backend_root)
    module_for_file: dict[Path, str] = {}

    all_defs_by_simple: dict[str, set[FunctionDefInfo]] = {}
    all_defs_by_module_simple: dict[tuple[str, str], set[FunctionDefInfo]] = {}

    for file_path in py_files:
        module = _module_name_for_file(backend_root, file_path)
        module_for_file[file_path] = module
        tree = _parse_file(file_path)
        if tree is None:
            continue
        collector = _DefCollector(module=module, file_path=file_path)
        collector.visit(tree)
        for info in collector.functions:
            simple = info.qualname.split(".")[-1]
            all_defs_by_simple.setdefault(simple, set()).add(info)
            all_defs_by_module_simple.setdefault((info.module, simple), set()).add(info)

    used_keys: set[str] = set()

    for file_path in py_files:
        module = module_for_file[file_path]
        tree = _parse_file(file_path)
        if tree is None:
            continue
        ref = _RefCollector()
        ref.visit(tree)

        for name in ref.called_names:
            for info in all_defs_by_simple.get(name, set()):
                used_keys.add(info.key)

        for base, attr in ref.called_attrs:
            for asname, imported_mod in ref.import_module_as:
                if base != asname:
                    continue
                if not _is_backend_module(imported_mod):
                    continue
                for info in all_defs_by_module_simple.get((imported_mod, attr), set()):
                    used_keys.add(info.key)

        # Build mapping from alias name -> (module, original_name)
        import_alias_map: dict[str, tuple[str, str]] = {}
        for imported_module, original_name, asname in ref.import_from:
            if not _is_backend_module(imported_module):
                continue
            import_alias_map[asname] = (imported_module, original_name)
            # Also mark as used if directly imported (original name used as-is)
            for info in all_defs_by_module_simple.get((imported_module, original_name), set()):
                used_keys.add(info.key)

        # Check if any called names match import aliases
        for called_name in ref.called_names:
            if called_name in import_alias_map:
                mod, orig = import_alias_map[called_name]
                for info in all_defs_by_module_simple.get((mod, orig), set()):
                    used_keys.add(info.key)

        # Mark methods as used when called via self.method(), cls.method(), super().method()
        # This catches polymorphic calls within class hierarchies
        for method_name in ref.called_method_names:
            for info in all_defs_by_simple.get(method_name, set()):
                used_keys.add(info.key)

        # Mark functions as used when passed as call arguments (callback references)
        for ref_name in ref.callback_refs:
            for info in all_defs_by_simple.get(ref_name, set()):
                used_keys.add(info.key)

    unused: list[FunctionDefInfo] = []
    for defs in all_defs_by_simple.values():
        for info in defs:
            simple = info.qualname.split(".")[-1]
            if not args.include_private and simple.startswith("_"):
                continue
            if info.key not in used_keys:
                unused.append(info)

    unused.sort(key=lambda x: (str(x.file_path), x.lineno, x.key))

    out_path = (repo_root / args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"backend root: {backend_root}")
    lines.append(f"python files scanned: {len(py_files)}")
    lines.append(f"candidate unused functions: {len(unused)}")
    lines.append("")

    for info in unused:
        rel = info.file_path.relative_to(repo_root)
        lines.append(f"{rel}:{info.lineno} {info.module}:{info.qualname}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(unused)} candidate unused functions to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
