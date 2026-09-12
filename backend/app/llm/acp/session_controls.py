"""ACP workspace, environment, and session control handling.

The historical ``runtime`` module remains the public facade. Dependencies are
synchronized before calls to preserve its established patching surface.
"""

from __future__ import annotations

# Extracted implementations retain intentional diagnostic assignments.
# ruff: noqa: F821, F841

from app.llm.acp import runtime as _compat_source

_COMPAT_DEPENDENCIES = {
    "resolve_workspace_path": ("Path",),
    "resolve_additional_directories": ("resolve_workspace_path",),
    "build_process_environment": ("os",),
    "extract_acp_session_controls": (
        "_field",
        "_legacy_mode_control",
        "_select_control",
        "_separate_combined_model_control",
    ),
    "serialize_acp_session_controls": (),
    "extract_model_config_option": ("extract_acp_session_controls",),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


for _dependency_name in (
    "Path",
    "_field",
    "_legacy_mode_control",
    "_select_control",
    "_separate_combined_model_control",
    "extract_acp_session_controls",
    "os",
    "resolve_workspace_path",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_resolve_workspace_path(workspace_root: str, relative_path: str) -> Path:
    """Resolve a model path while preventing traversal outside the trusted root."""
    root = Path(workspace_root).expanduser().resolve(strict=True)
    candidate = (root / (relative_path or ".")).resolve(strict=True)
    if candidate != root and root not in candidate.parents:
        raise ValueError("ACP workspace path escapes the configured workspace root")
    if not candidate.is_dir():
        raise ValueError("ACP workspace path is not a directory")
    return candidate


def _impl_resolve_additional_directories(
    workspace_root: str, paths: list[str] | None
) -> list[str]:
    """Resolve every additional directory through the same workspace boundary."""
    return [str(resolve_workspace_path(workspace_root, path)) for path in (paths or [])]


def _impl_build_process_environment(variable_names: list[str] | None) -> dict[str, str]:
    """Copy only explicitly allowed host variables in addition to SDK-safe defaults."""
    environment: dict[str, str] = {}
    for name in variable_names or []:
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _impl_extract_acp_session_controls(response: Any) -> dict[str, Any]:
    """Normalize model, security, and reasoning selectors from an ACP session."""
    config_options = _field(response, "config_options", "configOptions", [])
    model = _select_control(
        config_options,
        categories={"model"},
        id_hints={"model", "models", "model_id"},
    )
    security = _select_control(
        config_options,
        categories={"mode"},
        id_hints={"mode", "security", "security_level", "approval_mode"},
    )
    reasoning = _select_control(
        config_options,
        categories={"thought_level", "reasoning", "reasoning_effort"},
        id_hints={
            "thought_level",
            "reasoning",
            "reasoning_effort",
            "model_reasoning_effort",
        },
    )

    # Stable config options supersede legacy `modes` to avoid rendering and
    # setting the same semantic selector twice.
    if security is None:
        security = _legacy_mode_control(response)
    if model and reasoning is None:
        separated = _separate_combined_model_control(model)
        if separated:
            model, reasoning = separated
    return {
        "model": model,
        "security": security,
        "reasoning": reasoning,
    }


def _impl_serialize_acp_session_controls(controls: dict[str, Any]) -> dict[str, Any]:
    """Return the browser-safe public representation of normalized controls."""
    model = controls.get("model")
    security = controls.get("security")
    reasoning = controls.get("reasoning")
    return {
        "current_model_id": (str(model.get("current_value") or "") if model else None),
        "models": list(model.get("options") or []) if model else [],
        "current_security_level": (
            str(security.get("current_value") or "") if security else None
        ),
        "security_levels": (list(security.get("options") or []) if security else []),
        "current_reasoning_effort": (
            str(reasoning.get("current_value") or "") if reasoning else None
        ),
        "reasoning_efforts": (
            list(reasoning.get("options") or []) if reasoning else []
        ),
        # Older Codex agents encode model and effort in one value. Publishing
        # this compatibility matrix lets the browser exclude combinations the
        # agent never advertised instead of failing only when a turn starts.
        "reasoning_efforts_by_model": (
            dict(reasoning.get("reasoning_efforts_by_model") or {}) if reasoning else {}
        ),
    }


def _impl_extract_model_config_option(response: Any) -> dict[str, Any] | None:
    """Backward-compatible model-only view of normalized ACP controls."""
    model = extract_acp_session_controls(response).get("model")
    if not model:
        return None
    return {
        "config_id": model["config_id"],
        "current_model_id": model["current_value"],
        "models": model["options"],
    }
