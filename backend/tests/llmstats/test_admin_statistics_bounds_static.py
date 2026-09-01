import ast
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.llmstats import router as llmstats_router


REPO_ROOT = Path(__file__).resolve().parents[3]


def _source_for_function(relative_path: str, function_name: str) -> str:
    path = REPO_ROOT / relative_path
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{function_name} not found in {relative_path}")


def test_blocked_ip_list_is_paginated_and_page_scoped():
    router_source = (REPO_ROOT / "backend/app/ip_analytics/router.py").read_text()
    route_source = _source_for_function(
        "backend/app/ip_analytics/router.py",
        "list_blocked_ip_addresses_route",
    )
    orchestration_source = _source_for_function(
        "backend/app/ip_analytics/router.py",
        "list_blocked_ip_addresses_route",
    )
    persistence_source = _source_for_function(
        "backend/app/ip_analytics/models.py",
        "list_blocked_ips_page",
    )

    assert "response_model=AdminBlockedIPPage" in router_source
    assert "page: int = Query" in route_source
    assert "per_page: int = Query" in route_source
    assert ".offset((page - 1) * per_page)" in persistence_source
    assert ".limit(per_page)" in persistence_source
    assert (
        "page_ip_addresses = [entry.ip_address for entry in blocked_ips]"
        in orchestration_source
    )
    assert "BlockedIP.blocked_at.desc().nullslast()).all()" not in orchestration_source


def test_ip_statistics_overview_delegates_bounded_async_enrichment_and_rollups():
    function_source = _source_for_function(
        "backend/app/ip_analytics/router.py",
        "get_ip_address_statistics_overview_route",
    )

    assert "build_overview(" in function_source
    assert "background_tasks.add_task(" in function_source
    assert "enrich_pending_with_session_factory" in function_source
    assert "await get_country_by_ip" not in function_source


def test_admin_llm_dashboard_rollups_do_not_materialize_filtered_windows():
    for function_name in [
        "get_llm_stats_overview",
        "get_llm_stats_timeline",
        "get_llm_stats_by_provider",
        "get_llm_stats_by_model",
        "get_llm_stats_by_category",
        "get_llm_throughput_by_model",
        "get_llm_error_rates_by_model",
        "get_tool_call_stats_overview",
        "get_tool_call_stats_by_tool",
    ]:
        function_source = _source_for_function("backend/app/llmstats/router.py", function_name)
        assert "all_stats =" not in function_source
        assert "stats = query.all()" not in function_source
        assert "stats = _admin_llm_query" not in function_source
        assert "stats = _admin_tool_query" not in function_source
        assert ".with_entities(" in function_source


def test_admin_llm_statistics_query_excludes_user_managed_providers():
    """Every read-side dashboard query must hide private provider rows."""

    query = llmstats_router._admin_llm_query(Session())
    compiled = str(
        query.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "llm_generation_statistics.is_byok IS false" in compiled
    assert "NOT ((EXISTS" in compiled
    assert "llm_provider.id = llm_generation_statistics.provider_id" in compiled
    assert "FROM llm_provider" in compiled
    assert "FROM llm_provider, llm_generation_statistics" not in compiled
    assert "user_managed" in compiled
    assert "llm_generation_statistics.meta" in compiled


def test_admin_llm_statistics_maintenance_can_include_user_managed_providers():
    """Dashboard privacy filtering must not make private statistics undeletable."""

    query = llmstats_router._admin_llm_query(
        Session(),
        include_user_managed=True,
    )
    compiled = str(
        query.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "llm_generation_statistics.is_byok IS false" in compiled
    assert "user_managed" not in compiled


def test_realtime_export_streams_a_bounded_window():
    function_source = _source_for_function(
        "backend/app/llmstats/realtime_router.py",
        "export_realtime_statistics",
    )

    assert "days: int = Query(default=30, ge=1, le=365)" in function_source
    assert "created_at >= cutoff" in function_source
    assert ".yield_per(" in function_source
    assert "StreamingResponse" in function_source
    assert ".all()" not in function_source


def test_llm_statistics_do_not_include_provider_sync_exfiltration_endpoint():
    router_source = (REPO_ROOT / "backend/app/llmstats/router.py").read_text()
    models_source = (REPO_ROOT / "backend/app/llmstats/models.py").read_text()
    admin_html = (REPO_ROOT / "frontend/admin.html").read_text()
    admin_js = (REPO_ROOT / "frontend/js/admin/modelStatistics.js").read_text()

    combined_source = "\n".join([router_source, models_source, admin_html, admin_js])

    assert "omlorix-project/api/model-statistics" not in combined_source
    assert "/admin/send-to-provider" not in router_source
    assert "send-to-provider" not in admin_js
    assert "build_llm_stats_provider_payload" not in models_source
    assert "modelStatsProviderSyncBtn" not in admin_html


def test_statistics_import_routes_helpers_and_controls_are_removed():
    router_source = (REPO_ROOT / "backend/app/llmstats/router.py").read_text()
    models_source = (REPO_ROOT / "backend/app/llmstats/models.py").read_text()
    admin_html = (REPO_ROOT / "frontend/admin.html").read_text()
    admin_js = (REPO_ROOT / "frontend/js/admin/modelStatistics.js").read_text()
    byok_js = (REPO_ROOT / "frontend/js/chat/byok.js").read_text()

    for route in (
        'post("/admin/import")',
        'post("/admin/tool-calls/import")',
        'post("/admin/realtime/import")',
        'post("/user/byok/import")',
    ):
        assert route not in router_source

    assert "import_llm_generation_stats" not in models_source
    assert "import_tool_call_stats" not in models_source
    assert "modelStatsImportBtn" not in admin_html
    assert "realtimeStatsImportBtn" not in admin_html
    assert "/llmstats/admin/import" not in admin_js
    assert "/llmstats/admin/realtime/import" not in admin_js
    assert "byokStatisticsImport" not in byok_js
    assert "/llmstats/user/byok/import" not in byok_js


def test_group_statistics_by_user_excludes_byok_tool_counts():
    function_source = _source_for_function(
        "backend/app/llmstats/router.py",
        "get_group_statistics_by_user",
    )

    assert "_admin_tool_query(db)" in function_source
    assert "db.query(ToolCallStatistic.user_id, func.count(ToolCallStatistic.id))" not in function_source
