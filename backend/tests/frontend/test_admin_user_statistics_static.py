from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _select_contents(admin_html: str, select_id: str) -> str:
    """Return one admin select's markup so assertions cannot match another page."""
    select_start = f'<select id="{select_id}"'
    assert select_start in admin_html
    return admin_html.split(select_start, 1)[1].split("</select>", 1)[0]


def test_user_statistics_period_selects_offer_a_working_last_24_hours_range():
    """Keep the overview and detailed analytics filters aligned with model statistics."""
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
    user_statistics_js = (
        ROOT / "frontend" / "js" / "admin" / "userStatistics.js"
    ).read_text(encoding="utf-8")
    last_24_hours_option = (
        '<option value="1" data-i18n="period_last_24h">Last 24 hours</option>'
    )

    for select_id in ("userStatsPeriodSelect", "userStatsDetailPeriodSelect"):
        assert last_24_hours_option in _select_contents(admin_html, select_id)

    # The select value is parsed as a day count and forwarded by every user-statistics
    # request path; `1` therefore maps to the backend's rolling one-day cutoff.
    assert "state.period = parseInt(el.periodSelect.value, 10);" in user_statistics_js
    assert (
        "state.detailPeriod = parseInt(el.detailPeriodSelect.value, 10);"
        in user_statistics_js
    )
    assert "tracked-users-overview?days=${state.period}" in user_statistics_js
    assert "?days=${state.detailPeriod}" in user_statistics_js
