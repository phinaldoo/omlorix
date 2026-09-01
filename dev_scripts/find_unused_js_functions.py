#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "venv", "dist", "build"}


@dataclass(frozen=True)
class FunctionDef:
    name: str
    file: Path
    line: int
    kind: str
    signature: str


_FUNCTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "function",
        re.compile(
            r"^(?:export\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\((?P<args>[^)]*)\)\s*\{",
            re.MULTILINE,
        ),
    ),
    (
        "const_arrow",
        re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?\((?P<args>[^)]*)\)\s*=>\s*\{",
            re.MULTILINE,
        ),
    ),
    (
        "const_arrow_single",
        re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?(?P<args>[A-Za-z_$][A-Za-z0-9_$]*)\s*=>\s*\{",
            re.MULTILINE,
        ),
    ),
    (
        "const_function",
        re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?function\s*\((?P<args>[^)]*)\)\s*\{",
            re.MULTILINE,
        ),
    ),
]


def iter_js_files(js_root: Path) -> Iterable[Path]:
    for path in js_root.rglob("*.js"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def _line_number_from_pos(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def extract_function_defs(js_file: Path) -> list[FunctionDef]:
    try:
        content = js_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    defs: list[FunctionDef] = []
    for kind, pattern in _FUNCTION_PATTERNS:
        for match in pattern.finditer(content):
            name = match.group("name")
            args = match.groupdict().get("args", "")
            signature = f"{name}({args})"
            defs.append(
                FunctionDef(
                    name=name,
                    file=js_file,
                    line=_line_number_from_pos(content, match.start()),
                    kind=kind,
                    signature=signature,
                )
            )

    unique: dict[tuple[Path, int, str], FunctionDef] = {}
    for item in defs:
        unique[(item.file, item.line, item.name)] = item
    return sorted(unique.values(), key=lambda d: (str(d.file), d.line, d.name))


def build_reference_index(js_files: list[Path]) -> dict[str, list[tuple[Path, int]]]:
    index: dict[str, list[tuple[Path, int]]] = {}

    for path in js_files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for lineno, line in enumerate(content.splitlines(), start=1):
            for match in re.finditer(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", line):
                token = match.group(0)
                index.setdefault(token, []).append((path, lineno))

    return index


def is_likely_used(func: FunctionDef, ref_index: dict[str, list[tuple[Path, int]]]) -> bool:
    refs = ref_index.get(func.name, [])
    for ref_path, ref_line in refs:
        if ref_path != func.file:
            return True
        if ref_line != func.line:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--js-root", type=str, default="frontend/js")
    parser.add_argument("--output", type=str, default="temp/unused_js_functions.txt")
    parser.add_argument("--min-name-len", type=int, default=2)
    parser.add_argument("--ignore-leading-underscore", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    js_root = (repo_root / args.js_root).resolve()
    output_path = (repo_root / args.output).resolve()

    if not js_root.is_dir():
        print(f"JS root is not a valid directory: {js_root}", file=sys.stderr)
        return 2

    js_files = sorted(iter_js_files(js_root), key=lambda p: str(p))
    all_defs: list[FunctionDef] = []
    for path in js_files:
        all_defs.extend(extract_function_defs(path))

    filtered_defs: list[FunctionDef] = []
    for d in all_defs:
        if len(d.name) < args.min_name_len:
            continue
        if args.ignore_leading_underscore and d.name.startswith("_"):
            continue
        filtered_defs.append(d)

    ref_index = build_reference_index(js_files)

    unused = [d for d in filtered_defs if not is_likely_used(d, ref_index)]

    rel = lambda p: p.resolve().relative_to(repo_root.resolve()).as_posix()

    lines: list[str] = []
    lines.append(f"js_root: {rel(js_root)}")
    lines.append(f"files_scanned: {len(js_files)}")
    lines.append(f"function_defs_found: {len(filtered_defs)}")
    lines.append(f"unused_candidates: {len(unused)}")
    lines.append("")

    for d in unused:
        lines.append(f"{rel(d.file)}:{d.line}  {d.signature}  [{d.kind}]")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {output_path}")
    print(f"Unused candidates: {len(unused)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
