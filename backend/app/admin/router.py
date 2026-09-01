"""Compose the independently maintained administrator feature routers."""

from app.admin.audit_logs.router import admin_router as audit_logs_router
from app.admin.auth_diagnostics.router import admin_router as auth_diagnostics_router
from app.admin.chat_imports.router import admin_router as chat_imports_router
from app.admin.groups.router import admin_router as groups_router
from app.ip_analytics.router import admin_router as ip_analytics_router
from app.admin.notifications.router import admin_router as notifications_router
from app.admin.settings.router import admin_router as settings_router
from app.admin.user_exports.router import admin_router as user_exports_router
from app.admin.users.router import admin_router as users_router
from fastapi import APIRouter

_FEATURE_ROUTERS = (
    audit_logs_router,
    auth_diagnostics_router,
    groups_router,
    users_router,
    user_exports_router,
    chat_imports_router,
    ip_analytics_router,
    notifications_router,
    settings_router,
)

admin_router = APIRouter()
for _feature_router in _FEATURE_ROUTERS:
    admin_router.routes.extend(_feature_router.routes)
