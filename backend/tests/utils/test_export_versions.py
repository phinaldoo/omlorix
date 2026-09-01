"""Tests for JSON-safe export version comparison."""

from __future__ import annotations

import pytest

from app.utils.export_versions import matches_export_version


@pytest.mark.parametrize("value", [1, 1.0])
def test_equivalent_json_number_spellings_match(value) -> None:
    """Browser-normalized integer JSON must retain version compatibility."""

    assert matches_export_version(value, 1.0) is True


@pytest.mark.parametrize("value", [None, True, False, 0.9, 2, "1.0"])
def test_non_current_or_non_numeric_versions_do_not_match(value) -> None:
    """Only ordinary JSON numbers with the expected value are accepted."""

    assert matches_export_version(value, 1.0) is False
