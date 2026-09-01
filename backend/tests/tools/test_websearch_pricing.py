import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import Base
from app.llmstats.models import (
    ToolCallStatistic,
    create_tool_call_statistic,
    resolve_tool_call_cost,
)
from app.llmstats.router import (
    get_tool_call_stats_by_tool,
    get_tool_call_stats_overview,
)
from app.tools.websearch.pricing import (
    build_websearch_tool_meta,
    build_websearch_usage_event,
)


def test_only_exa_reported_cost_is_saved_as_tool_metadata():
    exa_cost = build_websearch_usage_event(
        provider=" Exa ",
        metadata={"cost": 0.012345, "ignored": "provider detail"},
    )
    ignored_cost = build_websearch_usage_event(
        provider="custom",
        metadata={"cost": 999, "secret": "do not persist"},
    )

    meta = build_websearch_tool_meta(
        base_meta={"truncated": True},
        usage_events=[exa_cost, ignored_cost],
    )

    assert exa_cost == {"cost": 0.012345}
    assert ignored_cost == {}
    assert meta == {"truncated": True, "cost": 0.012345}
    assert resolve_tool_call_cost(meta) == 0.012345


def test_exa_cost_reaches_admin_tool_call_statistics():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[ToolCallStatistic.__table__])
    db = sessionmaker(bind=engine)()

    meta = build_websearch_tool_meta(
        usage_events=[
            build_websearch_usage_event(provider="exa", metadata={"cost": 0.01}),
            build_websearch_usage_event(provider="exa", metadata={"cost": 0.02}),
        ]
    )
    statistic = create_tool_call_statistic(
        db=db,
        tool_name="web_search",
        success=True,
        meta=meta,
        is_byok=False,
    )

    overview = get_tool_call_stats_overview(db=db, admin_user=object(), days=30)
    by_tool = get_tool_call_stats_by_tool(db=db, admin_user=object(), days=30)

    assert statistic is not None
    assert statistic.meta == {"cost": 0.03}
    assert overview["estimated_total_cost"] == 0.03
    assert by_tool["tools"][0]["cost"] == 0.03

    db.close()
