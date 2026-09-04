import pytest

from app.tools.text_edits import apply_atomic_text_edits, select_text_content


def test_atomic_text_edits_resolve_all_ranges_against_one_snapshot():
    result = apply_atomic_text_edits(
        "Alpha old\nMiddle\nOmega old",
        [
            {
                "start_snippet": "Omega old",
                "end_snippet": "Omega old",
                "content": "Omega new",
            },
            {
                "start_snippet": "Alpha old",
                "end_snippet": "Alpha old",
                "content": "Alpha new and longer",
            },
        ],
        artifact_label="document",
    )

    assert result == "Alpha new and longer\nMiddle\nOmega new"


def test_atomic_text_edits_reject_overlapping_ranges_before_mutation():
    with pytest.raises(ValueError, match="overlap"):
        apply_atomic_text_edits(
            "Alpha\nBeta\nGamma",
            [
                {
                    "start_snippet": "Alpha",
                    "end_snippet": "Beta",
                    "content": "First",
                },
                {
                    "start_snippet": "Beta",
                    "end_snippet": "Gamma",
                    "content": "Second",
                },
            ],
            artifact_label="document",
        )


def test_targeted_text_reads_return_bounded_selection_metadata():
    content = "# Intro\nshort\n# Details\n" + ("important " * 100) + "\n# End\ndone"

    selected, metadata = select_text_content(
        content,
        heading="Details",
        max_chars=80,
    )

    assert selected.startswith("# Details")
    assert len(selected) == 80
    assert metadata["mode"] == "heading"
    assert metadata["total_chars"] == len(content)
    assert metadata["truncated"] is True


def test_query_read_keeps_match_inside_very_small_window():
    selected, metadata = select_text_content(
        "prefix NEEDLE suffix",
        query="needle",
        max_chars=3,
    )

    assert selected == "NEE"
    assert metadata["mode"] == "query"
    assert metadata["returned_chars"] == 3


def test_none_read_limit_uses_bounded_default():
    content = "x" * 25_000

    selected, metadata = select_text_content(content, max_chars=None)

    assert len(selected) == 20_000
    assert metadata["truncated"] is True


def test_targeted_text_reads_reject_oversized_selectors():
    with pytest.raises(ValueError, match="query must be 200 characters or fewer"):
        select_text_content("content", query="q" * 201)

    with pytest.raises(ValueError, match="heading must be 500 characters or fewer"):
        select_text_content("content", heading="h" * 501)
