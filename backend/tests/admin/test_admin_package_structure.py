"""Architecture checks for the feature-oriented administrator package."""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
ADMIN_ROOT = APP_ROOT / "admin"
ADMIN_ENDPOINT_FEATURE_MODULES = {
    "audit_logs": ("models.py", "schemas.py", "utils.py", "router.py"),
    "auth_diagnostics": ("schemas.py", "utils.py", "router.py"),
    "groups": ("models.py", "schemas.py", "router.py"),
    "users": ("models.py", "schemas.py", "utils.py", "router.py"),
    "user_exports": (
        "files/models.py",
        "jobs/utils.py",
        "schemas.py",
        "utils.py",
        "router.py",
    ),
    "chat_imports": ("models.py", "router.py"),
    "notifications": ("models.py", "schemas.py", "utils.py", "router.py"),
    "settings": (
        "models.py",
        "schema_categories/admin.py",
        "utils.py",
        "router.py",
    ),
}
# Transaction boundaries may be handled at the failure boundary; model helpers
# still own the actual persistence mutations.
FORBIDDEN_UTILS_DB_METHODS = {
    "query",
    "add",
    "delete",
    "flush",
    "refresh",
}


def test_admin_endpoint_features_keep_their_declared_module_boundaries():
    """Each endpoint feature keeps only the layers required by its responsibilities."""

    for feature, module_names in ADMIN_ENDPOINT_FEATURE_MODULES.items():
        feature_root = ADMIN_ROOT / feature
        for module_name in module_names:
            assert (feature_root / module_name).is_file(), (
                f"{feature}/{module_name} is missing"
            )


def test_admin_groups_page_is_consolidated_under_admin_package():
    """Prevent admin group routes and form schemas from drifting into core groups."""

    core_groups_root = APP_ROOT / "groups"
    assert not (core_groups_root / "router.py").exists()
    assert not (core_groups_root / "utils.py").exists()

    core_schema_tree = ast.parse((core_groups_root / "schemas.py").read_text())
    core_schema_names = {
        node.name
        for node in core_schema_tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert {
        "GroupFormSchema",
        "GroupCreate",
        "GroupValuesUpdatePayload",
    }.isdisjoint(core_schema_names)


def test_ip_analytics_is_consolidated_outside_the_admin_package():
    """Keep the complete IP analytics feature in its single top-level package."""

    feature_root = APP_ROOT / "ip_analytics"
    for module_name in (
        "models.py",
        "schemas.py",
        "service.py",
        "router.py",
    ):
        assert (feature_root / module_name).is_file(), (
            f"ip_analytics/{module_name} is missing"
        )
    assert not (ADMIN_ROOT / "ip_analytics").exists()


def test_admin_utils_do_not_access_sessions_directly():
    """Guard the persistence boundary in every administrator utility module."""

    violations: list[str] = []
    for utils_path in sorted(ADMIN_ROOT.rglob("utils.py")):
        utils_tree = ast.parse(utils_path.read_text())
        for node in ast.walk(utils_tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in FORBIDDEN_UTILS_DB_METHODS:
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id in {
                "db",
                "thread_db",
            }:
                relative_path = utils_path.relative_to(ADMIN_ROOT)
                violations.append(
                    f"{relative_path}:{node.lineno} calls db.{node.func.attr}()"
                )

    assert not violations, "\n".join(violations)


def test_root_admin_router_composes_all_feature_routes():
    """The application entry point must expose every feature router exactly once."""

    from app.admin import router as admin_router_module

    expected_routes = [
        route
        for feature_router in admin_router_module._FEATURE_ROUTERS
        for route in feature_router.routes
    ]

    assert admin_router_module.admin_router.routes == expected_routes
