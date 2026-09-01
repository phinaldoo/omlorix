from __future__ import annotations

from pathlib import Path
import re


_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")


def normalize_storage_key(storage_key: str) -> str:
    raw_key = str(storage_key or "").strip()
    if not raw_key:
        raise ValueError("storage_key is required")
    if raw_key.startswith("/"):
        raise ValueError("storage_key must be relative")
    if "\\" in raw_key:
        raise ValueError("storage_key contains invalid path separators")
    if _CONTROL_CHARACTER_RE.search(raw_key):
        raise ValueError("storage_key contains invalid control characters")

    normalized_parts: list[str] = []
    for part in raw_key.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("storage_key contains invalid traversal")
        normalized_parts.append(part)

    if not normalized_parts:
        raise ValueError("storage_key is required")
    return "/".join(normalized_parts)


def normalize_storage_component(component: str, *, field_name: str) -> str:
    raw_component = str(component or "").strip()
    if not raw_component:
        raise ValueError(f"{field_name} is required")
    if "/" in raw_component or "\\" in raw_component:
        raise ValueError(f"{field_name} contains invalid path separators")
    if raw_component in {".", ".."}:
        raise ValueError(f"{field_name} contains invalid traversal")
    if _CONTROL_CHARACTER_RE.search(raw_component):
        raise ValueError(f"{field_name} contains invalid control characters")
    return raw_component


def build_storage_prefix(user_id: str) -> str:
    return normalize_storage_component(user_id, field_name="user_id")


def ensure_user_scoped_storage_key(user_id: str, storage_key: str) -> str:
    normalized_key = normalize_storage_key(storage_key)
    prefix = build_storage_prefix(user_id)
    if not normalized_key.startswith(f"{prefix}/"):
        raise ValueError("storage_key does not belong to the user")
    return normalized_key


def resolve_local_storage_path(base_path: Path, storage_key: str, *, create_parent: bool = False) -> Path:
    normalized_key = normalize_storage_key(storage_key)
    base = Path(base_path).resolve()
    target = (base / Path(normalized_key)).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("Invalid local file storage key") from exc
    if create_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target
