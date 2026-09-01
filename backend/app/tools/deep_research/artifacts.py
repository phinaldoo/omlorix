from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from app.tools.deep_research.models import DeepResearchArtifact


_UNRESOLVED_REFERENCE_RE = re.compile(
    r"(?:sandbox:/mnt/data/|/tmp/output/|artifact://)[^\s)\]]+"
)
_REMOTE_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<url>https?://[^)\s]+)"
    r'(?:\s+"[^"]*")?\s*\)',
    re.IGNORECASE,
)


def _markdown_text(value: str) -> str:
    """Escape a small text value for use in Markdown labels."""

    return re.sub(r"([\\[\]*_`])", r"\\\1", str(value or ""))


def _candidate_references(artifact: DeepResearchArtifact) -> set[str]:
    """Return every model-visible reference that may identify an artifact."""

    original_path = str(artifact.original_filename or "").replace("\\", "/").lstrip("/")
    original_name = Path(original_path).name
    candidates = {
        f"artifact://{artifact.stable_id}",
        f"artifact://{original_name}",
        f"/tmp/output/{original_name}",
        f"/tmp/output/{original_path}",
    }
    return {candidate for candidate in candidates if candidate.rstrip("/")}


def _web_image_provenance(artifact: DeepResearchArtifact) -> str:
    """Render source attribution for a securely localized web image."""

    label = _markdown_text(artifact.attribution or "Web source")
    license_suffix = (
        f" · {_markdown_text(artifact.license_name)}"
        if artifact.license_name
        else ""
    )
    if artifact.source_url:
        return f"*Source: [{label}]({artifact.source_url}){license_suffix}.*"
    return f"*Source: {label}{license_suffix}.*"


def localize_artifact_references(
    report: str,
    artifacts: Iterable[DeepResearchArtifact],
    *,
    append_unreferenced: bool = True,
) -> str:
    """Replace model-visible artifact URIs with portable workspace paths."""

    artifact_list = list(artifacts)
    localized = str(report or "")
    for artifact in artifact_list:
        for candidate in _candidate_references(artifact):
            localized = localized.replace(candidate, artifact.relative_path)
        artifact_meta = artifact.meta if isinstance(artifact.meta, dict) else {}
        if (
            artifact_meta.get("origin") == "web_image"
            and artifact.relative_path in localized
            and artifact.source_url
        ):
            image_pattern = re.compile(
                rf"(!\[[^\]]*\]\({re.escape(artifact.relative_path)}"
                r'(?:\s+"[^"]*")?\))'
            )
            image_match = image_pattern.search(localized)
            if (
                image_match
                and artifact.source_url
                not in localized[image_match.end() : image_match.end() + 800]
            ):
                localized = (
                    localized[: image_match.end()]
                    + f"\n\n{_web_image_provenance(artifact)}"
                    + localized[image_match.end() :]
                )

    if not append_unreferenced:
        return localized

    unreferenced = [
        artifact for artifact in artifact_list if artifact.relative_path not in localized
    ]
    if not unreferenced:
        return localized

    lines = ["", "## Research artifacts", ""]
    for artifact in unreferenced:
        raw_label = (
            artifact.caption
            or Path(artifact.original_filename).stem.replace("_", " ").replace("-", " ")
            or artifact.stable_id
        )
        label = _markdown_text(raw_label[:120].strip())
        if artifact.kind == "image":
            artifact_meta = artifact.meta if isinstance(artifact.meta, dict) else {}
            provenance = (
                _web_image_provenance(artifact)
                if artifact_meta.get("origin") == "web_image"
                else "*Generated with the configured Code Execution service.*"
            )
            lines.extend(
                [
                    f"### {label}",
                    "",
                    f"![{_markdown_text(artifact.alt_text or raw_label)}]"
                    f"({artifact.relative_path})",
                    "",
                    provenance,
                    "",
                ]
            )
        else:
            lines.append(f"- [{label}]({artifact.relative_path})")
    return localized.rstrip() + "\n" + "\n".join(lines).rstrip() + "\n"


def clean_unresolved_artifact_references(report: str) -> tuple[str, list[str]]:
    """Neutralize generated-file references that were not persisted."""

    unresolved = sorted(set(_UNRESOLVED_REFERENCE_RE.findall(str(report or ""))))
    cleaned = str(report or "")
    for reference in unresolved:
        cleaned = re.sub(
            rf"!\[([^\]]*)\]\({re.escape(reference)}\)",
            r"> **Visual unavailable:** \1",
            cleaned,
        )
        cleaned = cleaned.replace(reference, "generated-artifact-unavailable")
    return cleaned, unresolved


def remove_remote_image_embeds(report: str) -> tuple[str, list[str]]:
    """Turn remote Markdown images into ordinary source links."""

    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        url = match.group("url")
        removed.append(url)
        label = _markdown_text(match.group("alt").strip() or url)
        return f"[{label}]({url})"

    return _REMOTE_IMAGE_RE.sub(replace, str(report or "")), removed
