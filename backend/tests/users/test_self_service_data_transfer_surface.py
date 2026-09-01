"""Regression tests for the single self-service account portability boundary."""

from app.automations.router import automations_router
from app.admin.router import admin_router
from app.chats.router import chats_router
from app.files.router import files_router
from app.memories.router import memories_router, project_memory_transfer_router
from app.notes.router import notes_router
from app.skills.router import skills_router
from app.todos.router import todo_router
from app.users.router import users_router


def _operations(router) -> set[tuple[str, str]]:
    """Return the concrete HTTP method/path pairs registered on a router."""
    return {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }


def test_self_service_transfer_exposes_complete_account_and_chatgpt_routes():
    """Keep category routes private while exposing the supported ChatGPT migration."""
    user_operations = _operations(users_router)
    assert ("GET", "/api/v1/users/export") in user_operations
    assert ("POST", "/api/v1/users/import/self") in user_operations
    assert ("POST", "/api/v1/users/import") not in user_operations

    account_category_routers = (
        automations_router,
        chats_router,
        files_router,
        memories_router,
        notes_router,
        skills_router,
        todo_router,
    )
    self_service_routes = {
        operation
        for router in account_category_routers
        for operation in _operations(router)
        if operation[1] in {
            "/api/v1/automations/export",
            "/api/v1/automations/import",
            "/api/v1/chats/export/self",
            "/api/v1/chats/import/self",
            "/api/v1/chats/import/chatgpt",
            "/api/v1/chats/import/openwebui",
            "/api/v1/files/export",
            "/api/v1/files/import",
            "/api/v1/memories/export",
            "/api/v1/memories/import",
            "/api/v1/notes/export",
            "/api/v1/notes/import",
            "/api/v1/skills/export",
            "/api/v1/skills/import",
            "/api/v1/todo/export",
            "/api/v1/todo/import",
        }
    }
    assert self_service_routes == {("POST", "/api/v1/chats/import/chatgpt")}


def test_native_chat_transfer_routes_are_removed_but_project_workflows_remain():
    """Only canonical user bundles may migrate native Omlorix chat data."""
    assert ("GET", "/api/v1/chats/export/all") not in _operations(chats_router)
    assert ("POST", "/api/v1/chats/import/all") not in _operations(chats_router)
    admin_operations = _operations(admin_router)
    assert ("POST", "/api/v1/admin/users/export/jobs") in admin_operations
    assert ("POST", "/api/v1/admin/users/import") in admin_operations
    assert ("POST", "/api/v1/admin/chats/export/jobs") not in admin_operations
    assert ("POST", "/api/v1/admin/import/openwebui/chats") in admin_operations
    assert (
        "GET",
        "/api/v1/projects/{project_id}/memories/export",
    ) not in _operations(project_memory_transfer_router)
    assert (
        "POST",
        "/api/v1/projects/{project_id}/memories/import",
    ) in _operations(project_memory_transfer_router)
