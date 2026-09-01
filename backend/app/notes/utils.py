from __future__ import annotations

from dataclasses import dataclass
import re


NOTE_REFERENCE_PATTERN = re.compile(
    r"\{\{note:(image|audio|file):([^:\|\}]+):([^|\}]+)(?:\|([^}]*?))?\}\}"
)

NOTE_MARKDOWN_LINK_PATTERN = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
OMLORIX_FILE_URL_PATTERN = re.compile(
    r"omlorix-file://([a-zA-Z0-9][a-zA-Z0-9._-]{0,127})([?#][^\s\"'<>)]*)?"
)
NOTE_INLINE_CODE_PATTERN = re.compile(r"`{1,3}")
NOTE_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
NOTE_BLOCKQUOTE_PATTERN = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
NOTE_LIST_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.MULTILINE)
NOTE_FORMATTING_PATTERN = re.compile(r"(\*\*|__|\*|_|~~)")
NOTE_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class NoteFileReference:
    kind: str
    owner_id: str
    file_id: str
    label: str
    raw_token: str


def parse_note_file_references(content: str | None) -> list[NoteFileReference]:
    references: list[NoteFileReference] = []
    source = str(content or "")
    for match in NOTE_REFERENCE_PATTERN.finditer(source):
        kind = str(match.group(1) or "").strip().lower()
        owner_id = str(match.group(2) or "").strip()
        file_id = str(match.group(3) or "").strip()
        label = str(match.group(4) or "").strip()
        if not kind or not owner_id or not file_id:
            continue
        references.append(
            NoteFileReference(
                kind=kind,
                owner_id=owner_id,
                file_id=file_id,
                label=label,
                raw_token=match.group(0),
            )
        )
    for match in NOTE_MARKDOWN_LINK_PATTERN.finditer(source):
        url = str(match.group(2) or "").strip()
        file_match = OMLORIX_FILE_URL_PATTERN.fullmatch(url)
        if not file_match:
            continue
        file_id = str(file_match.group(1) or "").strip()
        if not file_id:
            continue
        raw_token = match.group(0)
        references.append(
            NoteFileReference(
                kind="image" if raw_token.startswith("!") else "file",
                owner_id="",
                file_id=file_id,
                label=str(match.group(1) or "").strip(),
                raw_token=raw_token,
            )
        )
    return references


def note_content_contains_file_reference(content: str | None, *, owner_id: str, file_id: str) -> bool:
    target_owner = str(owner_id or "").strip()
    target_file = str(file_id or "").strip()
    if not target_owner or not target_file:
        return False
    return any(
        reference.file_id == target_file
        and (not reference.owner_id or reference.owner_id == target_owner)
        for reference in parse_note_file_references(content)
    )


def replace_note_file_references(
    content: str | None,
    replacements: dict[tuple[str, str, str], tuple[str, str]],
) -> str:
    source = str(content or "")

    def _replace(match: re.Match[str]) -> str:
        kind = str(match.group(1) or "").strip().lower()
        owner_id = str(match.group(2) or "").strip()
        file_id = str(match.group(3) or "").strip()
        label = str(match.group(4) or "").strip()
        next_ids = replacements.get((kind, owner_id, file_id))
        if not next_ids:
            return match.group(0)
        next_owner_id, next_file_id = next_ids
        suffix = f"|{label}" if label else ""
        return f"{{{{note:{kind}:{next_owner_id}:{next_file_id}{suffix}}}}}"

    replaced_source = NOTE_REFERENCE_PATTERN.sub(_replace, source)
    omlorix_file_replacements = {
        file_id: next_file_id
        for (_kind, _owner_id, file_id), (_next_owner_id, next_file_id) in replacements.items()
        if file_id and next_file_id
    }
    if not omlorix_file_replacements:
        return replaced_source

    def _replace_omlorix_file_url(match: re.Match[str]) -> str:
        file_id = str(match.group(1) or "").strip()
        replacement_file_id = omlorix_file_replacements.get(file_id)
        if not replacement_file_id:
            return match.group(0)
        suffix = str(match.group(2) or "")
        return f"omlorix-file://{replacement_file_id}{suffix}"

    return OMLORIX_FILE_URL_PATTERN.sub(_replace_omlorix_file_url, replaced_source)


def strip_note_file_references(content: str | None) -> str:
    return NOTE_REFERENCE_PATTERN.sub("", str(content or ""))


def note_content_to_plain_text(content: str | None) -> str:
    text = strip_note_file_references(content)
    text = NOTE_MARKDOWN_LINK_PATTERN.sub(lambda match: str(match.group(1) or ""), text)
    text = NOTE_INLINE_CODE_PATTERN.sub("", text)
    text = NOTE_HEADING_PATTERN.sub("", text)
    text = NOTE_BLOCKQUOTE_PATTERN.sub("", text)
    text = NOTE_LIST_PATTERN.sub("", text)
    text = NOTE_FORMATTING_PATTERN.sub("", text)
    text = NOTE_HTML_TAG_PATTERN.sub("", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
