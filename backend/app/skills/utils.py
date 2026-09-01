from __future__ import annotations

import base64
import io
import json
import logging
import re
import shutil
import uuid
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.paths import DATA_DIR
from app.skills.models import (
    ADMIN_SKILLS_USER_ID,
    create_admin_skill as create_admin_skill_db,
    create_skill as create_skill_db,
    list_admin_skills,
)

logger = logging.getLogger(__name__)

SKILLS_ROOT = DATA_DIR / "skills"
SKILL_IMPORT_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
SKILL_IMPORT_MAX_ENTRIES = 1000
SKILL_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
SKILL_IMPORT_MAX_FILE_BYTES = 10 * 1024 * 1024
SKILL_IMPORT_MAX_SKILL_MD_BYTES = 1024 * 1024
SKILL_IMPORT_MAX_COMPRESSION_RATIO = 100


def create_skill(
    db: Session,
    user_id: str,
    name: str,
    description: str,
    content: str,
    icon: str,
    compatibility: str | None = None,
    license: str | None = None,
    metadata: Mapping[str, str | int | float | None] | None = None,
    skill_markdown: str | None = None,
):
    """
    Persist a skill and generate the corresponding SKILL.md structure on disk.
    The folder hierarchy looks like:

        /app/data/skills/{user_id}/{skill_id}/SKILL.md

    SKILL.md follows https://agentskills.io/specification.
    """
    normalized_name = _require_value(name, "name")
    normalized_description = _require_value(description, "description")
    normalized_license = _normalize_optional(license)
    normalized_compatibility = _validate_compatibility(compatibility)
    normalized_metadata = _normalize_metadata(metadata)

    skill = create_skill_db(db, user_id, normalized_name, normalized_description, content, icon)

    skill_dir: Path | None = None
    try:
        skill_dir = _prepare_skill_directory(user_id, skill.id)
        markdown_payload = skill_markdown or _build_skill_markdown(
            name=normalized_name,
            description=normalized_description,
            license_value=normalized_license,
            compatibility=normalized_compatibility,
            metadata=normalized_metadata,
            body_content=content,
        )
        (skill_dir / "SKILL.md").write_text(markdown_payload, encoding="utf-8")
    except Exception:
        db.delete(skill)
        db.commit()
        if skill_dir is not None:
            shutil.rmtree(skill_dir, ignore_errors=True)
        raise

    return skill


def _prepare_skill_directory(user_id: str, skill_id: str) -> Path:
    """Prepare the skill directory for a user's skill."""
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    safe_user = _safe_path_segment(user_id, "user_id")
    safe_skill_id = _safe_path_segment(skill_id, "skill_id")
    skill_dir = SKILLS_ROOT / safe_user / safe_skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    return skill_dir


def _build_skill_markdown(
    name: str,
    description: str,
    license_value: str | None,
    compatibility: str | None,
    metadata: dict[str, str],
    body_content: str,
) -> str:
    """Build the SKILL.md markdown content."""
    lines: list[str] = ["---"]
    lines.extend(_format_yaml_field("name", name))
    lines.extend(_format_yaml_field("description", description))

    if license_value:
        lines.extend(_format_yaml_field("license", license_value))

    if compatibility:
        lines.extend(_format_yaml_field("compatibility", compatibility))

    if metadata:
        lines.append("metadata:")
        for key in sorted(metadata):
            value = metadata[key]
            nested_lines = _format_yaml_field(key, value, indent_level=1)
            lines.extend(nested_lines)

    lines.append("---")

    body = body_content.strip()
    if body:
        lines.append("")
        lines.append(body)
        lines.append("")
    else:
        lines.append("")

    return "\n".join(lines)


def _format_yaml_field(key: str, value: str, *, indent_level: int = 0) -> list[str]:
    """Format a YAML field with proper indentation."""
    value_str = str(value)
    indent = "  " * indent_level
    if "\n" in value_str:
        formatted = [f"{indent}{key}: |"]
        formatted.extend(f"{indent}  {line}" for line in value_str.splitlines())
        return formatted

    if value_str == "":
        serialized = '""'
    else:
        serialized = json.dumps(value_str)
    return [f"{indent}{key}: {serialized}"]


def _normalize_optional(value: str | None) -> str | None:
    """Normalize optional string value."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _validate_compatibility(value: str | None) -> str | None:
    """Validate and normalize compatibility string."""
    normalized = _normalize_optional(value)
    if normalized is None:
        return None
    if not (1 <= len(normalized) <= 500):
        raise ValueError("compatibility must be between 1 and 500 characters when provided")
    return normalized


def _normalize_metadata(metadata: Mapping[str, str | int | float | None] | None) -> dict[str, str]:
    """Normalize metadata to string values."""
    if not metadata:
        return {}

    normalized: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("metadata keys cannot be empty")
        normalized[normalized_key] = "" if value is None else str(value)
    return normalized


def _safe_path_segment(value: str, field_name: str) -> str:
    """Sanitize a value for use in a filesystem path."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if any(sep in cleaned for sep in ("/", "\\")):
        raise ValueError(f"{field_name} cannot contain path separators")
    return cleaned


def _require_value(value: str, field_name: str) -> str:
    """Require a non-empty string value."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    return cleaned


def load_skill_markdown_fields(user_id: str, skill_id: str) -> dict[str, object]:
    """
    Read SKILL.md for the given user/skill and return parsed frontmatter fields.
    """
    skill_path = _skill_storage_path(user_id, skill_id) / "SKILL.md"
    if not skill_path.exists():
        return {}
    try:
        markdown_text = skill_path.read_text(encoding="utf-8")
        frontmatter, _ = _parse_skill_markdown(markdown_text)
    except Exception:  # pragma: no cover - malformed files should not crash the API
        return {}

    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        metadata = None
    author = None
    if metadata is not None and metadata.get("author") is not None:
        normalized_author = str(metadata["author"]).strip()
        author = normalized_author or None

    return {
        "name": frontmatter.get("name"),
        "description": frontmatter.get("description"),
        "license": frontmatter.get("license"),
        "compatibility": frontmatter.get("compatibility"),
        "metadata": metadata,
        "author": author,
    }


def write_skill_markdown_file(
    user_id: str,
    skill_id: str,
    *,
    name: str,
    description: str,
    content: str | None,
    license_value: str | None,
    compatibility: str | None,
    metadata: Mapping[str, str | int | float | None] | None,
):
    """
    Persist SKILL.md with the provided fields, ensuring on-disk structure stays in sync.
    """
    normalized_license = _normalize_optional(license_value)
    normalized_compatibility = _validate_compatibility(compatibility)
    normalized_metadata = _normalize_metadata(metadata)
    skill_dir = _prepare_skill_directory(user_id, skill_id)
    markdown_payload = _build_skill_markdown(
        name=name,
        description=description,
        license_value=normalized_license,
        compatibility=normalized_compatibility,
        metadata=normalized_metadata,
        body_content=(content or ""),
    )
    (skill_dir / "SKILL.md").write_text(markdown_payload, encoding="utf-8")


def import_skill_from_markdown(db: Session, user_id: str, markdown_text: str):
    """
    Import a single skill from raw SKILL.md markdown text.
    Validates the frontmatter structure and creates the skill.
    Returns the created skill DB object.
    """
    frontmatter, body_content = _parse_skill_markdown(markdown_text)

    name = frontmatter.get("name")
    description = frontmatter.get("description")

    if not name or not str(name).strip():
        raise ValueError("SKILL.md is missing required 'name' field in frontmatter")
    if not description or not str(description).strip():
        raise ValueError("SKILL.md is missing required 'description' field in frontmatter")

    name = str(name).strip()
    description = str(description).strip()

    # Validate name pattern
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", name):
        raise ValueError(
            f"Skill name '{name}' is invalid. Use lowercase letters, numbers, and hyphens only (e.g., my-skill-name)"
        )

    metadata_block = frontmatter.get("metadata")
    metadata = metadata_block if isinstance(metadata_block, dict) else None
    license_value = frontmatter.get("license")
    compatibility_value = frontmatter.get("compatibility")
    _default_icon = json.dumps({
        "preset": "tool",
        "color": "#E53935",
    })
    icon = _extract_icon_from_metadata(metadata) or _default_icon

    skill = create_skill(
        db=db,
        user_id=user_id,
        name=name,
        description=description,
        content=body_content or "",
        icon=icon,
        compatibility=str(compatibility_value).strip() if compatibility_value else None,
        license=str(license_value).strip() if license_value else None,
        metadata=metadata,
        skill_markdown=markdown_text,
    )
    return skill


def import_admin_skill_from_markdown(
    db: Session,
    markdown_text: str,
    *,
    expected_folder_name: str | None = None,
):
    """
    Import one admin skill from a standards-compatible ``SKILL.md`` document.

    The original Markdown is preserved byte-for-byte after UTF-8 decoding so
    optional Agent Skills frontmatter fields that Omlorix does not render remain
    portable on the next export. Archive imports may also require the
    frontmatter name to match the containing directory, as required by the
    Agent Skills specification.
    """
    frontmatter, body_content = _parse_skill_markdown(markdown_text)
    name, description, compatibility, license_value, metadata = _validate_import_frontmatter(
        frontmatter,
        expected_folder_name=expected_folder_name,
    )

    default_icon = json.dumps({"preset": "tool", "color": "#E53935"})
    icon = _extract_icon_from_metadata(metadata) or default_icon
    skill = create_admin_skill_db(
        db=db,
        name=name,
        description=description,
        content=body_content or "",
        icon=icon,
    )

    skill_dir: Path | None = None
    try:
        skill_dir = _prepare_skill_directory(ADMIN_SKILLS_USER_ID, skill.id)
        (skill_dir / "SKILL.md").write_text(markdown_text, encoding="utf-8")
    except Exception:
        # Admin skill creation commits immediately, so explicitly compensate if
        # writing its portable on-disk representation fails.
        db.delete(skill)
        db.commit()
        if skill_dir is not None:
            shutil.rmtree(skill_dir, ignore_errors=True)
        raise

    return skill


def _validate_import_frontmatter(
    frontmatter: Mapping[str, object],
    *,
    expected_folder_name: str | None = None,
) -> tuple[str, str, str | None, str | None, Mapping[str, object] | None]:
    """Validate the Agent Skills fields needed to persist an imported skill."""
    raw_name = frontmatter.get("name")
    raw_description = frontmatter.get("description")
    if raw_name is None or not str(raw_name).strip():
        raise ValueError("SKILL.md is missing required 'name' field in frontmatter")
    if raw_description is None or not str(raw_description).strip():
        raise ValueError("SKILL.md is missing required 'description' field in frontmatter")

    name = str(raw_name).strip()
    description = str(raw_description).strip()
    if len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError(
            f"Skill name '{name}' is invalid. Use at most 64 lowercase letters, numbers, and hyphens"
        )
    if len(description) > 1024:
        raise ValueError("Skill description exceeds the maximum length of 1024 characters")
    if expected_folder_name is not None and expected_folder_name != name:
        raise ValueError(
            f"Skill folder '{expected_folder_name}' must match the SKILL.md name '{name}'"
        )

    compatibility = _validate_compatibility(
        str(frontmatter["compatibility"]) if frontmatter.get("compatibility") is not None else None
    )
    license_value = _normalize_optional(
        str(frontmatter["license"]) if frontmatter.get("license") is not None else None
    )
    metadata_value = frontmatter.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, Mapping) else None
    return name, description, compatibility, license_value, metadata


def export_admin_skills_archive(db: Session) -> tuple[io.BytesIO, int]:
    """
    Return all admin skills as an Agent Skills-compatible ZIP archive.

    Every top-level directory is a complete skill package. Existing
    ``SKILL.md`` documents and all sibling resources are preserved.
    """
    buffer = io.BytesIO()
    exported = 0
    used_names: set[str] = set()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for skill in list_admin_skills(db):
            skill_dir = _skill_storage_path(ADMIN_SKILLS_USER_ID, skill.id)
            markdown_path = skill_dir / "SKILL.md"
            try:
                # An administrator may have one incomplete or externally
                # damaged package on disk. Keep that package from preventing
                # every other managed skill from being downloaded.
                markdown_text = markdown_path.read_text(encoding="utf-8")
                package_name = _resolve_export_package_name(
                    markdown_text=markdown_text,
                    used_names=used_names,
                )
            except (OSError, ValueError):
                logger.warning(
                    "Skipping admin skill %s during export: unreadable or invalid SKILL.md",
                    skill.id,
                    exc_info=True,
                )
                continue
            used_names.add(package_name)

            frontmatter, _body = _parse_skill_markdown(markdown_text)
            markdown_name = str(frontmatter.get("name") or "").strip()
            if markdown_name != package_name:
                rewritten_markdown_text = _rewrite_skill_markdown_name(
                    markdown_text,
                    package_name,
                )
                # Some valid YAML forms, including a block scalar ``name``,
                # cannot be rewritten by the line-preserving helper. Retain
                # the source document instead of exporting an empty SKILL.md.
                if rewritten_markdown_text:
                    markdown_text = rewritten_markdown_text
            markdown_text = _upsert_skill_markdown_metadata_string(
                markdown_text,
                key="omlorix_icon",
                value=skill.icon,
            )

            _write_admin_skill_package_to_zip(
                archive=archive,
                skill_dir=skill_dir,
                package_name=package_name,
                markdown_text=markdown_text,
            )
            exported += 1

    buffer.seek(0)
    return buffer, exported


def _resolve_export_package_name(
    *,
    markdown_text: str,
    used_names: set[str],
) -> str:
    """Choose a valid, unique Agent Skills directory/frontmatter name."""
    frontmatter, _body = _parse_skill_markdown(markdown_text)
    base_name = slugify_skill_name(str(frontmatter.get("name") or ""))

    candidate = base_name
    suffix = 2
    while candidate in used_names:
        suffix_text = f"-{suffix}"
        candidate = f"{base_name[: 64 - len(suffix_text)].rstrip('-')}{suffix_text}"
        suffix += 1
    return candidate


def _rewrite_skill_markdown_name(markdown_text: str, package_name: str) -> str:
    """Replace the top-level frontmatter name while retaining all other text."""
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""

    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            current_value = line.split(":", 1)[1].strip()
            # Replacing only the declaration line of a YAML block/folded
            # scalar would leave its indented value behind and corrupt the
            # frontmatter. Signal the caller to retain the source document.
            if current_value.startswith(("|", ">")):
                return ""
            lines[index] = _format_yaml_field("name", package_name)[0]
            suffix = "\n" if markdown_text.endswith("\n") else ""
            return "\n".join(lines) + suffix
    return ""


def _upsert_skill_markdown_metadata_string(
    markdown_text: str,
    *,
    key: str,
    value: str,
) -> str:
    """
    Add one string metadata value without rebuilding the imported document.

    Agent Skills explicitly permits implementation-specific metadata. Keeping
    Omlorix's icon there makes an export/import round trip lossless while
    retaining unknown standard fields such as ``allowed-tools``.
    """
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown_text

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        return markdown_text

    metadata_index = next(
        (
            index
            for index, line in enumerate(lines[1:closing_index], start=1)
            if line == "metadata:"
        ),
        None,
    )
    serialized_value = _format_yaml_field(key, value)[0]
    if metadata_index is None:
        # Preserve valid but unsupported inline YAML metadata rather than
        # introducing a duplicate key into its frontmatter.
        if any(
            line.startswith("metadata:") for line in lines[1:closing_index]
        ):
            return markdown_text
        lines[closing_index:closing_index] = ["metadata:", f"  {serialized_value}"]
    else:
        block_end = metadata_index + 1
        existing_index = None
        metadata_indent = None
        while block_end < closing_index:
            line = lines[block_end]
            if line and not line.startswith((" ", "\t")):
                break
            if metadata_indent is None and line.strip():
                indent_match = re.match(r"^([ \t]+)", line)
                if indent_match:
                    metadata_indent = indent_match.group(1)
            if (
                metadata_indent is not None
                and re.match(
                    rf"^{re.escape(metadata_indent)}{re.escape(key)}\s*:",
                    line,
                )
            ):
                existing_index = block_end
            block_end += 1
        metadata_indent = metadata_indent or "  "
        serialized_line = f"{metadata_indent}{serialized_value}"
        if existing_index is not None:
            lines[existing_index] = serialized_line
        else:
            lines.insert(block_end, serialized_line)

    suffix = "\n" if markdown_text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _write_admin_skill_package_to_zip(
    *,
    archive: zipfile.ZipFile,
    skill_dir: Path,
    package_name: str,
    markdown_text: str,
) -> None:
    """Write one complete standards-compatible admin skill package."""
    archive.writestr(f"{package_name}/", "")
    archive.writestr(f"{package_name}/SKILL.md", markdown_text.encode("utf-8"))
    if not skill_dir.is_dir():
        return

    for path in skill_dir.rglob("*"):
        if path == skill_dir / "SKILL.md":
            continue
        # Never follow a server-side symlink into unrelated data while
        # constructing an administrator download. Check every component
        # because some traversal implementations can yield descendants of a
        # symlinked directory even when the descendant itself is not a link.
        source_relative = path.relative_to(skill_dir)
        source_cursor = skill_dir
        has_symlink_component = False
        for part in source_relative.parts:
            source_cursor /= part
            if source_cursor.is_symlink():
                has_symlink_component = True
                break
        if has_symlink_component:
            continue
        relative = Path(package_name) / source_relative
        arcname = relative.as_posix()
        if path.is_dir():
            archive.writestr(f"{arcname}/", "")
        elif path.is_file():
            archive.write(path, arcname)


def import_admin_skills_archive(
    db: Session,
    zip_bytes: bytes,
    *,
    selected_folder_prefixes: set[str] | None = None,
):
    """
    Import one or more complete admin skill directories from a ZIP archive.

    Archive expansion is bounded by the same entry, file, aggregate-size, and
    compression-ratio limits used for personal skills. Each skill is isolated:
    a malformed package is reported without discarding successfully imported
    siblings, and a failed package is fully rolled back.
    """
    if len(zip_bytes) > SKILL_IMPORT_MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the maximum allowed size")

    created: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid zip archive") from exc

    with archive:
        _validate_archive_limits(archive)
        all_skill_docs = [
            info for info in archive.infolist() if PurePosixPath(info.filename).name.lower() == "skill.md"
        ]
        if not all_skill_docs:
            raise ValueError("Archive does not contain any SKILL.md files")
        skill_docs = all_skill_docs
        if selected_folder_prefixes is not None:
            skill_docs = [
                info
                for info in all_skill_docs
                if PurePosixPath(info.filename).parent.as_posix() in selected_folder_prefixes
            ]
            if not skill_docs:
                raise ValueError("Archive does not contain any selected skill folders")

        for info in skill_docs:
            skill_path = PurePosixPath(info.filename)
            folder_prefix = skill_path.parent.as_posix()
            created_skill = None
            try:
                if folder_prefix in {"", "."}:
                    raise ValueError("Each SKILL.md in an archive must be inside its own skill folder")
                if info.file_size > SKILL_IMPORT_MAX_SKILL_MD_BYTES:
                    raise ValueError("SKILL.md exceeds maximum allowed size")

                markdown_text = archive.read(info).decode("utf-8-sig")
                created_skill = import_admin_skill_from_markdown(
                    db,
                    markdown_text,
                    expected_folder_name=skill_path.parent.name,
                )
                _write_archive_skill_assets(
                    archive=archive,
                    folder_prefix=folder_prefix,
                    destination=_skill_storage_path(ADMIN_SKILLS_USER_ID, created_skill.id),
                )
                created.append({"id": created_skill.id, "name": created_skill.name})
            except Exception as exc:  # pylint: disable=broad-except
                db.rollback()
                if created_skill is not None:
                    db.delete(created_skill)
                    db.commit()
                    shutil.rmtree(
                        _skill_storage_path(ADMIN_SKILLS_USER_ID, created_skill.id),
                        ignore_errors=True,
                    )
                errors.append({"entry": info.filename, "error": str(exc)})

    return {"created": created, "errors": errors}


def _skill_storage_path(user_id: str, skill_id: str) -> Path:
    """Get the storage path for a skill."""
    return SKILLS_ROOT / _safe_path_segment(user_id, "user_id") / _safe_path_segment(skill_id, "skill_id")


def _write_archive_skill_assets(archive: zipfile.ZipFile, folder_prefix: str, destination: Path) -> None:
    """
    Copy any files that live alongside SKILL.md into the destination folder.
    If the archive does not group files inside a folder, only SKILL.md is preserved (as it was already written).
    """
    if not folder_prefix or folder_prefix == ".":
        return

    for info in archive.infolist():
        if info.is_dir():
            continue
        if not info.filename.startswith(f"{folder_prefix}/"):
            continue
        relative_str = info.filename[len(folder_prefix) + 1 :]
        if not relative_str or PurePosixPath(relative_str).name.lower() == "skill.md":
            continue
        relative_path = _validate_archive_relative_path(relative_str)
        target_path = _safe_destination_path(destination, relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(target_path, "wb") as target:
            shutil.copyfileobj(source, target)


def _validate_archive_limits(archive: zipfile.ZipFile) -> None:
    """Validate archive size and compression limits."""
    infos = archive.infolist()
    if len(infos) > SKILL_IMPORT_MAX_ENTRIES:
        raise ValueError("Archive contains too many files")

    total_uncompressed = 0
    for info in infos:
        _validate_archive_member_path(info.filename)
        if info.is_dir():
            continue

        if info.file_size > SKILL_IMPORT_MAX_FILE_BYTES:
            raise ValueError(f"Archive entry '{info.filename}' exceeds maximum allowed size")

        total_uncompressed += max(0, int(info.file_size or 0))
        if total_uncompressed > SKILL_IMPORT_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("Archive uncompressed contents exceed maximum allowed size")

        compressed = int(info.compress_size or 0)
        if compressed > 0:
            ratio = info.file_size / compressed
            if ratio > SKILL_IMPORT_MAX_COMPRESSION_RATIO:
                raise ValueError(f"Archive entry '{info.filename}' exceeds compression ratio limit")


def _validate_archive_member_path(raw_path: str) -> None:
    """Validate archive member path for security."""
    if "\x00" in raw_path:
        raise ValueError("Archive contains invalid file path")

    normalized = raw_path.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized.startswith("/"):
        raise ValueError(f"Archive entry '{raw_path}' has an invalid path")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"Archive entry '{raw_path}' attempts path traversal")


def _validate_archive_relative_path(raw_path: str) -> PurePosixPath:
    """Validate and convert archive relative path."""
    _validate_archive_member_path(raw_path)
    return PurePosixPath(raw_path.replace("\\", "/").strip())


def _safe_destination_path(destination: Path, relative_path: PurePosixPath) -> Path:
    """Compute a safe destination path within the destination root."""
    destination_root = destination.resolve()
    candidate = (destination_root / Path(*relative_path.parts)).resolve()
    if candidate != destination_root and destination_root not in candidate.parents:
        raise ValueError("Archive entry resolves outside destination")
    return candidate


def _parse_skill_markdown(markdown_text: str) -> tuple[dict, str]:
    """Parse skill markdown into frontmatter and body."""
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with a frontmatter block (---)")

    idx = 1
    frontmatter_lines: list[str] = []
    while idx < len(lines):
        if lines[idx].strip() == "---":
            idx += 1
            break
        frontmatter_lines.append(lines[idx])
        idx += 1
    else:
        raise ValueError("SKILL.md frontmatter must end with '---'")

    frontmatter = _parse_frontmatter(frontmatter_lines)
    body = "\n".join(lines[idx:]).strip()
    return frontmatter, body


def _parse_frontmatter(lines: list[str]) -> dict:
    """Parse YAML frontmatter lines into a dict."""
    data: dict[str, object] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            idx += 1
            continue

        if stripped == "metadata:":
            idx += 1
            metadata: dict[str, object] = {}
            while idx < len(lines):
                nested = lines[idx]
                if not nested.startswith("  "):
                    break
                key, value = _split_key_value(nested.strip())
                metadata[key] = _parse_scalar(value)
                idx += 1
            data["metadata"] = metadata
            continue

        if stripped.endswith(": |"):
            key = stripped[:-3].strip()
            idx += 1
            block_lines: list[str] = []
            while idx < len(lines):
                nested = lines[idx]
                if nested.startswith("  "):
                    block_lines.append(nested[2:])
                    idx += 1
                elif not nested.strip():
                    block_lines.append("")
                    idx += 1
                else:
                    break
            data[key] = "\n".join(block_lines).rstrip("\n")
            continue

        key, value = _split_key_value(stripped)
        data[key] = _parse_scalar(value)
        idx += 1

    return data


def _split_key_value(line: str) -> tuple[str, str]:
    """Split a YAML key-value line."""
    if ":" not in line:
        raise ValueError(f"Invalid frontmatter line '{line}'")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_scalar(value: str) -> str | int | float | bool | None:
    """Parse a YAML scalar value."""
    if value in {"", '""'}:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _extract_icon_from_metadata(metadata: Mapping[str, object] | None) -> str | None:
    """Extract icon from metadata."""
    if not metadata:
        return None
    for key in ("omlorix_icon", "icon"):
        icon_value = metadata.get(key)
        if isinstance(icon_value, str) and icon_value.strip():
            return icon_value.strip()
    return None


def slugify_skill_name(value: str) -> str:
    """Return a skill-safe slug from arbitrary text."""
    lowered = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    normalized = lowered.strip("-")
    if not normalized:
        raise ValueError("name is required")
    if len(normalized) > 64:
        normalized = normalized[:64].rstrip("-")
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", normalized):
        raise ValueError("name must use lowercase letters, numbers, and hyphens only")
    return normalized


def build_skill_markdown_document(
    *,
    name: str,
    description: str,
    content: str,
    compatibility: str | None = None,
    license_value: str | None = None,
    metadata: Mapping[str, str | int | float | None] | None = None,
) -> str:
    """Public helper used by draft generation and save flows."""
    return _build_skill_markdown(
        name=slugify_skill_name(name),
        description=_require_value(description, "description"),
        license_value=_normalize_optional(license_value),
        compatibility=_validate_compatibility(compatibility),
        metadata=_normalize_metadata(metadata),
        body_content=content or "",
    )


def build_skill_draft_payload(
    db: Session,
    *,
    user_id: str,
    name: str,
    description: str,
    content: str,
    icon: str | None = None,
    compatibility: str | None = None,
    license_value: str | None = None,
    metadata: Mapping[str, str | int | float | None] | None = None,
    files: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a normalized payload for the interactive skill draft widget."""
    normalized_name = slugify_skill_name(name)
    normalized_description = _require_value(description, "description")
    normalized_icon = (
        str(icon).strip()
        if isinstance(icon, str) and str(icon).strip()
        else json.dumps(
            {
                "preset": "tool",
                "color": "#E53935",
            }
        )
    )
    normalized_files = normalize_skill_draft_files(db, user_id=user_id, files=files)
    skill_markdown = build_skill_markdown_document(
        name=normalized_name,
        description=normalized_description,
        content=content or "",
        compatibility=compatibility,
        license_value=license_value,
        metadata=metadata,
    )
    return {
        "draft_id": str(uuid.uuid4()),
        "name": normalized_name,
        "description": normalized_description,
        "icon": normalized_icon,
        "compatibility": _validate_compatibility(compatibility),
        "license": _normalize_optional(license_value),
        "metadata": _normalize_metadata(metadata),
        "skill_markdown": skill_markdown,
        "files": normalized_files,
        "file_count": len(normalized_files),
    }


def _is_text_editable_media_type(media_type: str | None) -> bool:
    normalized = str(media_type or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("text/")
        or normalized in {
            "application/json",
            "application/xml",
            "application/yaml",
            "application/x-yaml",
            "application/javascript",
            "application/typescript",
            "image/svg+xml",
        }
        or normalized.endswith("+json")
        or normalized.endswith("+xml")
    )


def normalize_skill_draft_files(
    db: Session,
    *,
    user_id: str,
    files: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Validate and enrich draft files for widget rendering or saving."""
    normalized_files: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()

    if not files:
        return normalized_files
    if len(files) > SKILL_DRAFT_MAX_FILES:
        raise ValueError(f"Too many draft files. Maximum is {SKILL_DRAFT_MAX_FILES}.")

    for raw_file in files:
        if not isinstance(raw_file, Mapping):
            raise ValueError("Each draft file must be an object.")

        folder_type = _validate_folder_type(str(raw_file.get("folder_type") or "").strip())
        filename = _validate_filename(str(raw_file.get("filename") or ""))
        dedupe_key = (folder_type, filename.lower())
        if dedupe_key in seen_paths:
            raise ValueError(f"Duplicate draft file '{folder_type}/{filename}'.")
        seen_paths.add(dedupe_key)

        source_file_id = str(raw_file.get("source_file_id") or "").strip() or None
        content = raw_file.get("content")
        encoding = str(raw_file.get("encoding") or "utf-8").strip().lower() or "utf-8"
        if encoding not in {"utf-8", "base64"}:
            raise ValueError("Draft file encoding must be 'utf-8' or 'base64'.")
        if source_file_id and content not in (None, ""):
            raise ValueError(f"Draft file '{filename}' cannot define both content and source_file_id.")

        media_type = str(raw_file.get("media_type") or "").strip() or None
        description = str(raw_file.get("description") or "").strip() or None
        draft_file: dict[str, Any] = {
            "folder_type": folder_type,
            "filename": filename,
            "encoding": encoding,
            "media_type": media_type,
            "description": description,
        }

        if source_file_id:
            from app.files.models import get_file
            from app.files.utils import materialize_file_record

            file_record = get_file(db, source_file_id, user_id)
            if not file_record:
                raise ValueError(f"Referenced file '{source_file_id}' was not found.")
            resolved_media_type = media_type or str(file_record.file_type or "").strip() or None
            file_size = getattr(file_record, "file_size", None)
            if file_size is None:
                materialized_path = materialize_file_record(file_record, user_id)
                file_size = materialized_path.stat().st_size
            else:
                file_size = int(file_size)
            if file_size > SKILL_IMPORT_MAX_FILE_BYTES:
                raise ValueError(f"Referenced file '{file_record.file_name}' exceeds the maximum draft file size of {SKILL_IMPORT_MAX_FILE_BYTES} bytes.")
            if _is_text_editable_media_type(resolved_media_type):
                if file_size > SKILL_DRAFT_MAX_TEXT_BYTES:
                    raise ValueError(f"Referenced file '{file_record.file_name}' exceeds the maximum editable draft size.")
                materialized_path = materialize_file_record(file_record, user_id)
                source_bytes = materialized_path.read_bytes()
                try:
                    text_content = source_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"Referenced file '{file_record.file_name}' could not be decoded as UTF-8.") from exc
                draft_file.update(
                    {
                        "kind": "inline_text",
                        "encoding": "utf-8",
                        "content": text_content,
                        "resolved_media_type": resolved_media_type,
                        "size": len(source_bytes),
                        "source_file_id": file_record.id,
                        "source_file_name": file_record.file_name,
                    }
                )
                normalized_files.append(draft_file)
                continue

            preview_url = None
            if str(file_record.file_category or "").strip().lower() == "image":
                preview_url = f"/api/v1/files/download?file_id={file_record.id}&inline=true"
            draft_file.update(
                {
                    "kind": "source_file",
                    "source_file_id": file_record.id,
                    "source_file_name": file_record.file_name,
                    "source_file_size": file_size,
                    "source_file_category": file_record.file_category,
                    "resolved_media_type": resolved_media_type,
                    "preview_url": preview_url,
                }
            )
            normalized_files.append(draft_file)
            continue

        if content is None:
            text_content = ""
        elif isinstance(content, str):
            text_content = content
        else:
            raise ValueError(f"Draft file '{filename}' content must be a string.")

        if encoding == "base64":
            try:
                decoded_bytes = base64.b64decode(text_content.encode("utf-8"), validate=True)
            except Exception as exc:  # pragma: no cover - precise decoder exception is not important
                raise ValueError(f"Draft file '{filename}' has invalid base64 content.") from exc
            if len(decoded_bytes) > SKILL_DRAFT_MAX_TEXT_BYTES:
                raise ValueError(f"Draft file '{filename}' exceeds the maximum draft file size.")
            try:
                text_content = decoded_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"Draft file '{filename}' must decode to UTF-8 text for inline editing.") from exc
            encoding = "utf-8"

        if len(text_content.encode("utf-8")) > SKILL_DRAFT_MAX_TEXT_BYTES:
            raise ValueError(f"Draft file '{filename}' exceeds the maximum draft file size.")

        draft_file.update(
            {
                "kind": "inline_text",
                "encoding": encoding,
                "content": text_content,
                "resolved_media_type": media_type,
                "size": len(text_content.encode("utf-8")),
            }
        )
        normalized_files.append(draft_file)

    return normalized_files


def save_skill_draft(
    db: Session,
    *,
    user_id: str,
    skill_markdown: str,
    icon: str | None = None,
    files: list[Mapping[str, Any]] | None = None,
):
    """Persist a reviewed skill draft into the real skills workspace."""
    markdown_text = str(skill_markdown or "")
    if not markdown_text.strip():
        raise ValueError("skill_markdown is required")
    if len(markdown_text.encode("utf-8")) > SKILL_DRAFT_MAX_MARKDOWN_BYTES:
        raise ValueError("SKILL.md exceeds the maximum allowed size")

    frontmatter, body_content = _parse_skill_markdown(markdown_text)
    name = slugify_skill_name(str(frontmatter.get("name") or ""))
    description = _require_value(str(frontmatter.get("description") or ""), "description")
    metadata_block = frontmatter.get("metadata")
    metadata = metadata_block if isinstance(metadata_block, Mapping) else None
    normalized_icon = (
        str(icon).strip()
        if isinstance(icon, str) and str(icon).strip()
        else _extract_icon_from_metadata(metadata)
    ) or json.dumps(
        {
            "preset": "tool",
            "color": "#E53935",
        }
    )

    normalized_files = normalize_skill_draft_files(db, user_id=user_id, files=files)
    skill = create_skill(
        db=db,
        user_id=user_id,
        name=name,
        description=description,
        content=body_content or "",
        icon=normalized_icon,
        compatibility=str(frontmatter.get("compatibility") or "").strip() or None,
        license=str(frontmatter.get("license") or "").strip() or None,
        metadata=metadata,
        skill_markdown=markdown_text,
    )

    try:
        for draft_file in normalized_files:
            file_bytes_or_path, is_path = _resolve_skill_draft_file_bytes(db, user_id=user_id, draft_file=draft_file)
            upload_skill_file(
                user_id=user_id,
                skill_id=skill.id,
                folder_type=str(draft_file["folder_type"]),
                filename=str(draft_file["filename"]),
                content=file_bytes_or_path if not is_path else None,
                source_path=file_bytes_or_path if is_path else None,
            )
    except Exception:
        db.delete(skill)
        db.commit()
        shutil.rmtree(_skill_storage_path(user_id, skill.id), ignore_errors=True)
        raise

    return skill


def _resolve_skill_draft_file_bytes(
    db: Session,
    *,
    user_id: str,
    draft_file: Mapping[str, Any],
) -> tuple[bytes | Path, bool]:
    """
    Resolve the on-disk bytes or path to store for a draft file.
    Returns (bytes_or_path, is_path) where is_path is True if the first element is a Path.
    """
    kind = str(draft_file.get("kind") or "").strip()
    if kind == "source_file":
        from app.files.models import get_file
        from app.files.utils import materialize_file_record

        source_file_id = str(draft_file.get("source_file_id") or "").strip()
        file_record = get_file(db, source_file_id, user_id)
        if not file_record:
            raise ValueError(f"Referenced file '{source_file_id}' was not found.")
        file_size = getattr(file_record, "file_size", None)
        if file_size is None:
            materialized_path = materialize_file_record(file_record, user_id)
            file_size = materialized_path.stat().st_size
        else:
            file_size = int(file_size)
        if file_size > SKILL_IMPORT_MAX_FILE_BYTES:
            raise ValueError(f"Referenced file '{file_record.file_name}' exceeds the maximum draft file size of {SKILL_IMPORT_MAX_FILE_BYTES} bytes.")
        return (materialize_file_record(file_record, user_id), True)

    content = draft_file.get("content")
    if not isinstance(content, str):
        raise ValueError(f"Draft file '{draft_file.get('filename')}' content must be text.")
    return (content.encode("utf-8"), False)


# ============================================================================
# Agent Skills File Management (scripts/, references/, assets/)
# ============================================================================

ALLOWED_SKILL_FOLDERS = {"scripts", "references", "assets"}
SKILL_DRAFT_MAX_FILES = 48
SKILL_DRAFT_MAX_TEXT_BYTES = 512 * 1024
SKILL_DRAFT_MAX_MARKDOWN_BYTES = 1024 * 1024


def _validate_folder_type(folder_type: str) -> str:
    """Validate that the folder type is one of the allowed types."""
    if folder_type not in ALLOWED_SKILL_FOLDERS:
        raise ValueError(f"Invalid folder type '{folder_type}'. Must be one of: {', '.join(ALLOWED_SKILL_FOLDERS)}")
    return folder_type


def _validate_filename(filename: str) -> str:
    """Validate filename to prevent path traversal attacks."""
    if not filename:
        raise ValueError("Filename cannot be empty")
    cleaned = filename.strip()
    if not cleaned:
        raise ValueError("Filename cannot be empty")
    if any(sep in cleaned for sep in ("/", "\\", "..")):
        raise ValueError("Invalid filename")
    if cleaned.startswith("."):
        raise ValueError("Hidden files are not allowed")
    return cleaned


def list_skill_files(user_id: str, skill_id: str, folder_type: str) -> list[dict[str, str | int]]:
    """
    List all files in a skill's folder (scripts/, references/, or assets/).
    Returns a list of dicts with 'name' and 'size' keys.
    """
    _validate_folder_type(folder_type)
    skill_dir = _skill_storage_path(user_id, skill_id)
    folder_path = skill_dir / folder_type

    if not folder_path.exists():
        return []

    files = []
    for file_path in folder_path.iterdir():
        if file_path.is_file():
            files.append({
                "name": file_path.name,
                "size": file_path.stat().st_size,
            })
    return sorted(files, key=lambda f: f["name"])


def upload_skill_file(
    user_id: str,
    skill_id: str,
    folder_type: str,
    filename: str,
    content: bytes | None = None,
    source_path: Path | None = None,
) -> dict[str, str | int]:
    """
    Upload a file to a skill's folder (scripts/, references/, or assets/).
    Either content or source_path must be provided.
    Returns dict with 'name' and 'size' keys.
    """
    _validate_folder_type(folder_type)
    safe_filename = _validate_filename(filename)

    skill_dir = _skill_storage_path(user_id, skill_id)
    folder_path = skill_dir / folder_type
    folder_path.mkdir(parents=True, exist_ok=True)

    file_path = folder_path / safe_filename
    temp_path = file_path.with_name(f".{safe_filename}.{uuid.uuid4().hex}.tmp")
    if source_path is not None:
        try:
            shutil.copyfile(source_path, temp_path)
            temp_path.replace(file_path)
        finally:
            temp_path.unlink(missing_ok=True)
        file_size = file_path.stat().st_size
    elif content is not None:
        try:
            temp_path.write_bytes(content)
            temp_path.replace(file_path)
        finally:
            temp_path.unlink(missing_ok=True)
        file_size = len(content)
    else:
        raise ValueError("Either content or source_path must be provided")

    return {
        "name": safe_filename,
        "size": file_size,
    }


def delete_skill_file(user_id: str, skill_id: str, folder_type: str, filename: str) -> bool:
    """
    Delete a file from a skill's folder (scripts/, references/, or assets/).
    Returns True if deleted, raises ValueError if not found.
    """
    _validate_folder_type(folder_type)
    safe_filename = _validate_filename(filename)
    
    skill_dir = _skill_storage_path(user_id, skill_id)
    file_path = skill_dir / folder_type / safe_filename
    
    if not file_path.exists() or not file_path.is_file():
        raise ValueError(f"File '{filename}' not found in {folder_type}/")
    
    file_path.unlink()
    
    # Clean up empty folder
    folder_path = skill_dir / folder_type
    if folder_path.exists() and not any(folder_path.iterdir()):
        folder_path.rmdir()
    
    return True


def get_all_skill_files(user_id: str, skill_id: str) -> dict[str, list[dict[str, str | int]]]:
    """
    Get all files from all three folders for a skill.
    Returns a dict with 'scripts', 'references', and 'assets' keys.
    """
    return {
        "scripts": list_skill_files(user_id, skill_id, "scripts"),
        "references": list_skill_files(user_id, skill_id, "references"),
        "assets": list_skill_files(user_id, skill_id, "assets"),
    }
