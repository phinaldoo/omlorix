#!/usr/bin/env python3
"""Script to find undefined CSS variables (custom properties) across the codebase.

Scans all CSS files, collects:
- Defined variables: occurrences of `--foo: ...;`
- Referenced variables: occurrences of `var(--foo, ...)`

Outputs a text report listing any referenced variables that were not defined in any CSS file.
"""

import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Set, Tuple


# Add parent directory to path to import from app if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "venv"}


@dataclass(frozen=True)
class Location:
    file: Path
    line: int
    col: int
    line_text: str


_VAR_REF_RE = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)\b")
# This matches typical custom property definitions. It intentionally ignores `--foo` occurrences
# that are not definitions (e.g. inside `var(--foo)` or comments).
_VAR_DEF_RE = re.compile(r"(^|[\s;{])(--[a-zA-Z0-9_-]+)\s*:")
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def find_css_files(root_dir: Path) -> List[Path]:
    css_files: List[Path] = []
    for file_path in root_dir.rglob("*.css"):
        if any(skip_dir in file_path.parts for skip_dir in SKIP_DIRS):
            continue
        css_files.append(file_path)
    return sorted(css_files)


def _strip_block_comments(text: str) -> str:
    return _COMMENT_BLOCK_RE.sub("", text)


def _iter_lines_with_numbers(text: str) -> Iterable[Tuple[int, str]]:
    for idx, line in enumerate(text.splitlines(), start=1):
        yield idx, line


def extract_defined_vars(css_file: Path) -> Tuple[Set[str], DefaultDict[str, List[Location]]]:
    defined: Set[str] = set()
    locations: DefaultDict[str, List[Location]] = defaultdict(list)

    try:
        raw = css_file.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"Error reading {css_file}: {e}", file=sys.stderr)
        return defined, locations

    content = _strip_block_comments(raw)

    for line_no, line in _iter_lines_with_numbers(content):
        for match in _VAR_DEF_RE.finditer(line):
            var_name = match.group(2)
            defined.add(var_name)
            locations[var_name].append(
                Location(
                    file=css_file,
                    line=line_no,
                    col=match.start(2) + 1,
                    line_text=line.rstrip("\n"),
                )
            )

    return defined, locations


def extract_referenced_vars(css_file: Path) -> Tuple[Set[str], DefaultDict[str, List[Location]]]:
    referenced: Set[str] = set()
    locations: DefaultDict[str, List[Location]] = defaultdict(list)

    try:
        raw = css_file.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"Error reading {css_file}: {e}", file=sys.stderr)
        return referenced, locations

    content = _strip_block_comments(raw)

    for line_no, line in _iter_lines_with_numbers(content):
        for match in _VAR_REF_RE.finditer(line):
            var_name = match.group(1)
            referenced.add(var_name)
            locations[var_name].append(
                Location(
                    file=css_file,
                    line=line_no,
                    col=match.start(1) + 1,
                    line_text=line.rstrip("\n"),
                )
            )

    return referenced, locations


def write_report(
    *,
    root_dir: Path,
    output_file: Path,
    missing_vars: Set[str],
    ref_locations: Dict[str, List[Location]],
) -> None:
    with output_file.open("w", encoding="utf-8") as f:
        if not missing_vars:
            f.write("All referenced CSS variables are defined in CSS files.\n")
            return

        f.write("Undefined CSS variables (referenced via var(--...))\n")
        f.write("=" * 60 + "\n\n")

        for var_name in sorted(missing_vars):
            f.write(f"{var_name}\n")
            for loc in ref_locations.get(var_name, [])[:50]:
                rel = loc.file.relative_to(root_dir)
                f.write(f"  {rel}:{loc.line}:{loc.col}: {loc.line_text.strip()}\n")
            extra = max(0, len(ref_locations.get(var_name, [])) - 50)
            if extra:
                f.write(f"  ... (+{extra} more occurrences)\n")
            f.write("\n")


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent

    print("Finding CSS files...")
    css_files = find_css_files(root_dir)
    print(f"Found {len(css_files)} CSS files")

    defined_vars: Set[str] = set()
    referenced_vars: Set[str] = set()

    defined_locations: DefaultDict[str, List[Location]] = defaultdict(list)
    referenced_locations: DefaultDict[str, List[Location]] = defaultdict(list)

    print("\nScanning CSS variables...")
    for i, css_file in enumerate(css_files):
        if (i + 1) % 50 == 0:
            print(f"  Scanning file {i+1}/{len(css_files)}...", end="\r")

        defs, def_locs = extract_defined_vars(css_file)
        refs, ref_locs = extract_referenced_vars(css_file)

        defined_vars.update(defs)
        referenced_vars.update(refs)

        for k, v in def_locs.items():
            defined_locations[k].extend(v)
        for k, v in ref_locs.items():
            referenced_locations[k].extend(v)

    print(f"  Scanned {len(css_files)} files          ")

    missing_vars = referenced_vars - defined_vars

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Defined variables: {len(defined_vars)}")
    print(f"Referenced variables: {len(referenced_vars)}")
    print(f"Missing variables: {len(missing_vars)}")

    output_file = root_dir / "temp" / "undefined_css_variables.txt"
    write_report(
        root_dir=root_dir,
        output_file=output_file,
        missing_vars=missing_vars,
        ref_locations=referenced_locations,
    )

    if missing_vars:
        print(f"\nUndefined variables found. Report saved to: {output_file}")
        return 1

    print(f"\nNo undefined variables found. Report saved to: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
