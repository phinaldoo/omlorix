from __future__ import annotations

from dataclasses import dataclass
import json
import re

from app.tools.deep_research.schemas import ArticleRevision


class AnchoredEditError(ValueError):
    """Raised when a model-proposed article patch cannot be applied safely."""


_EDIT_NUMBER_RE = re.compile(r"\bEdit\s+(\d+)\b", re.IGNORECASE)
_REPAIR_EXCERPT_BEFORE_CHARS = 1_500
_REPAIR_EXCERPT_AFTER_CHARS = 6_500


@dataclass(frozen=True, slots=True)
class _ResolvedEdit:
    """One validated edit range resolved against the unchanged source article."""

    index: int
    start: int
    end: int
    replacement: str


def _unique_snippet_index(content: str, snippet: str, *, label: str) -> int:
    """Return the only exact occurrence of a snippet or reject ambiguity."""

    first_index = content.find(snippet)
    if first_index < 0:
        raise AnchoredEditError(f"{label} was not found in the existing article.")
    if content.find(snippet, first_index + 1) >= 0:
        raise AnchoredEditError(
            f"{label} matched more than once; provide a longer unique snippet."
        )
    return first_index


def _unique_end_index(
    content: str,
    snippet: str,
    *,
    search_from: int,
    label: str,
) -> int:
    """Resolve exactly one end anchor occurring after its start anchor."""

    first_index = content.find(snippet, search_from)
    if first_index < 0:
        raise AnchoredEditError(f"{label} was not found after start_snippet.")
    if content.find(snippet, first_index + 1) >= 0:
        raise AnchoredEditError(
            f"{label} matched more than once after start_snippet; "
            "provide a longer unique snippet."
        )
    return first_index


def _resolve_article_edits(
    article: str,
    revision: ArticleRevision,
) -> list[_ResolvedEdit]:
    """Resolve and validate every proposed edit against the original article."""

    source = str(article or "")
    if revision.edits and not source:
        raise AnchoredEditError("Cannot apply anchored edits to an empty article.")

    resolved: list[_ResolvedEdit] = []
    for edit_index, edit in enumerate(revision.edits, start=1):
        label_prefix = f"Edit {edit_index}"
        start = _unique_snippet_index(
            source,
            edit.start_snippet,
            label=f"{label_prefix} start_snippet",
        )
        if edit.start_snippet == edit.end_snippet:
            end = start + len(edit.end_snippet)
        else:
            end_start = _unique_end_index(
                source,
                edit.end_snippet,
                search_from=start + len(edit.start_snippet),
                label=f"{label_prefix} end_snippet",
            )
            end = end_start + len(edit.end_snippet)

        if start == 0 and end == len(source):
            raise AnchoredEditError(
                f"{label_prefix} targets the whole article; use smaller local edits."
            )

        span_length = end - start
        if len(source) >= 2_000 and span_length > int(len(source) * 0.5):
            raise AnchoredEditError(
                f"{label_prefix} replaces more than half of the article."
            )
        if len(edit.replacement_markdown) > max(4_000, int(len(source) * 0.6)):
            raise AnchoredEditError(
                f"{label_prefix} replacement is too large for a targeted edit."
            )
        if source.strip() and source.strip() in edit.replacement_markdown:
            raise AnchoredEditError(
                f"{label_prefix} embeds the whole existing article in one replacement."
            )
        resolved.append(
            _ResolvedEdit(
                index=edit_index,
                start=start,
                end=end,
                replacement=edit.replacement_markdown,
            )
        )

    resolved.sort(key=lambda item: (item.start, item.end))
    for previous, current in zip(resolved, resolved[1:]):
        if current.start < previous.end:
            raise AnchoredEditError(
                f"Edits {previous.index} and {current.index} overlap."
            )

    total_replaced = sum(item.end - item.start for item in resolved)
    if len(source) >= 2_000 and total_replaced > int(len(source) * 0.7):
        raise AnchoredEditError("The edit plan replaces more than 70% of the article.")
    total_replacement = sum(len(item.replacement) for item in resolved)
    if total_replacement > max(8_000, int(len(source) * 0.8)):
        raise AnchoredEditError(
            "The edit plan returns too much replacement text for a targeted revision."
        )
    return resolved


def validate_article_revision(article: str, revision: ArticleRevision) -> None:
    """Validate an article edit plan without mutating or returning content."""

    _resolve_article_edits(article, revision)


def article_revision_repair_context(
    article: str,
    revision: ArticleRevision | None,
    validation_summary: str,
) -> str:
    """Build focused, verbatim context for repairing one invalid edit plan.

    Exact-anchor failures are much easier to correct when the model receives
    the rejected edit and the relevant source excerpt instead of searching a
    complete long report again. The excerpt deliberately preserves whitespace,
    punctuation, and Markdown links because anchors must copy them byte-for-byte.
    """

    source = str(article or "")
    summary = str(validation_summary or "")
    rejected_edit = None
    if isinstance(revision, ArticleRevision) and revision.edits:
        match = _EDIT_NUMBER_RE.search(summary)
        edit_index = int(match.group(1)) - 1 if match else 0
        if 0 <= edit_index < len(revision.edits):
            rejected_edit = revision.edits[edit_index]

    anchor = rejected_edit.start_snippet if rejected_edit is not None else ""
    anchor_index = source.find(anchor) if anchor else -1
    if anchor_index < 0 and rejected_edit is not None:
        anchor = rejected_edit.end_snippet
        anchor_index = source.find(anchor)
    if anchor_index < 0:
        excerpt_start = 0
        excerpt_end = min(len(source), _REPAIR_EXCERPT_AFTER_CHARS)
    else:
        excerpt_start = max(0, anchor_index - _REPAIR_EXCERPT_BEFORE_CHARS)
        excerpt_end = min(
            len(source),
            anchor_index + len(anchor) + _REPAIR_EXCERPT_AFTER_CHARS,
        )

    rejected_json = (
        json.dumps(rejected_edit.model_dump(), ensure_ascii=False, indent=2)
        if rejected_edit is not None
        else "The validator could not isolate one edit; inspect every anchor."
    )
    excerpt = source[excerpt_start:excerpt_end]
    return (
        "Article anchor repair requirements:\n"
        "- Copy every start_snippet and end_snippet verbatim from the current article.\n"
        "- Do not add, remove, or alter citations, punctuation, whitespace, or links in anchors.\n"
        "- Change only invalid edits; preserve valid edits and their replacement content.\n\n"
        f"Rejected edit:\n{rejected_json}\n\n"
        "Verbatim current-article excerpt:\n"
        f"{excerpt}"
    )


def apply_article_revision(article: str, revision: ArticleRevision) -> str:
    """Apply non-overlapping anchored edits while preserving untouched text."""

    revised = str(article or "")
    resolved = _resolve_article_edits(revised, revision)
    # Offsets were resolved against the original text, so reverse application
    # keeps every earlier offset stable and makes the result deterministic.
    for edit in reversed(resolved):
        revised = f"{revised[: edit.start]}{edit.replacement}{revised[edit.end :]}"
    return revised
