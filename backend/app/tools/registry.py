from __future__ import annotations

from typing import Any


RATE_LIMIT_TOOL_LABEL_I18N_KEYS: dict[str, str] = {
    "create_visualization": "rate_limit_tool_label_create_visualization",
    "subagent": "rate_limit_tool_label_subagent",
    "web_search": "rate_limit_tool_label_web_search",
    "weather": "rate_limit_tool_label_weather",
    "flashcards": "rate_limit_tool_label_flashcards",
    "quiz": "rate_limit_tool_label_quiz",
    "todos": "rate_limit_tool_label_todos",
    "notes": "rate_limit_tool_label_notes",
    "automations": "rate_limit_tool_label_automations",
    "skills": "rate_limit_tool_label_skills",
    "memories": "rate_limit_tool_label_memories",
    "image_generation": "rate_limit_tool_label_image_generation",
    "video_generation": "rate_limit_tool_label_video_generation",
    "audio_generation": "rate_limit_tool_label_audio_generation",
    "music_generation": "rate_limit_tool_label_music_generation",
    "canvas": "rate_limit_tool_label_canvas",
    "slide_presentation": "rate_limit_tool_label_slide_presentation",
    "deep_research": "rate_limit_tool_label_deep_research",
    "latex_pdf": "rate_limit_tool_label_latex_pdf",
    "code_execution": "rate_limit_tool_label_code_execution",
}

RATE_LIMIT_TOOL_DESCRIPTION_I18N_KEYS: dict[str, str] = {
    "create_visualization": "rate_limit_tool_description_create_visualization",
    "web_search": "rate_limit_tool_description_web_search",
    "weather": "rate_limit_tool_description_weather",
    "flashcards": "rate_limit_tool_description_flashcards",
    "quiz": "rate_limit_tool_description_quiz",
    "todos": "rate_limit_tool_description_todos",
    "notes": "rate_limit_tool_description_notes",
    "automations": "rate_limit_tool_description_automations",
    "skills": "rate_limit_tool_description_skills",
    "memories": "rate_limit_tool_description_memories",
    "image_generation": "rate_limit_tool_description_image_generation",
    "video_generation": "rate_limit_tool_description_video_generation",
    "audio_generation": "rate_limit_tool_description_audio_generation",
    "music_generation": "rate_limit_tool_description_music_generation",
    "canvas": "rate_limit_tool_description_canvas",
    "slide_presentation": "rate_limit_tool_description_slide_presentation",
    "deep_research": "rate_limit_tool_description_deep_research",
    "code_execution": "rate_limit_tool_description_code_execution",
}


BUILTIN_RATE_LIMIT_TOOLS: tuple[dict[str, str], ...] = (
    {"key": "create_visualization", "label": "Visualization", "description": "Create an inline interactive visualization.", "source": "built_in"},
    {"key": "web_search", "label": "Web search", "description": "Search and retrieve web or image results.", "source": "built_in"},
    {"key": "weather", "label": "Weather", "description": "Fetch weather conditions and forecasts.", "source": "built_in"},
    {"key": "flashcards", "label": "Flashcards", "description": "Create flashcard study widgets.", "source": "built_in"},
    {"key": "quiz", "label": "Quiz", "description": "Create quiz study widgets.", "source": "built_in"},
    {"key": "image_generation", "label": "Image generation", "description": "Generate images.", "source": "built_in"},
    {"key": "video_generation", "label": "Video generation", "description": "Generate videos.", "source": "built_in"},
    {"key": "audio_generation", "label": "Audio generation", "description": "Generate audio.", "source": "built_in"},
    {"key": "music_generation", "label": "Music generation", "description": "Generate music.", "source": "built_in"},
    {"key": "todos", "label": "Todos", "description": "Read and manage todos.", "source": "built_in"},
    {"key": "notes", "label": "Notes", "description": "Read and manage notes.", "source": "built_in"},
    {
        "key": "automations",
        "label": "Automations",
        "description": "Automations run saved prompts on chosen models by schedule or webhook; call type='information' first.",
        "source": "built_in",
    },
    {"key": "skills", "label": "Skills", "description": "Read and use skills.", "source": "built_in"},
    {"key": "memories", "label": "Memories", "description": "Read and manage memories.", "source": "built_in"},
    {"key": "canvas", "label": "Canvas", "description": "Create and save canvas artifacts.", "source": "built_in"},
    {"key": "slide_presentation", "label": "Slide presentation", "description": "Create or edit slide presentations.", "source": "built_in"},
    {"key": "deep_research", "label": "Deep research", "description": "Run a deep research workflow.", "source": "built_in"},
    {
        "key": "latex_pdf",
        "label": "LaTeX rendering",
        "description": "Compile Canvas LaTeX previews and PDF downloads.",
        "source": "built_in",
    },
    {"key": "code_execution", "label": "Code execution", "description": "Execute code in the sandbox.", "source": "built_in"},
)

TOOL_KEY_ALIASES = {
    "code_execution_internal": "code_execution",
    "slide_presentation_legacy": "slide_presentation",
    "get_weather": "weather",
    "create_flashcards": "flashcards",
    "create_quiz": "quiz",
}


def normalize_rate_limit_tool_key(tool_key: str | None) -> str:
    raw = str(tool_key or "").strip()
    return TOOL_KEY_ALIASES.get(raw, raw)


def get_rate_limit_tool_label_i18n_key(tool_key: str | None) -> str | None:
    """Return a hardcoded translation key for a known built-in tool label."""
    return RATE_LIMIT_TOOL_LABEL_I18N_KEYS.get(normalize_rate_limit_tool_key(tool_key))


def get_rate_limit_tool_description_i18n_key(tool_key: str | None) -> str | None:
    """Return a hardcoded translation key for a known built-in tool description."""
    return RATE_LIMIT_TOOL_DESCRIPTION_I18N_KEYS.get(normalize_rate_limit_tool_key(tool_key))


def _decorate_tool(tool: dict[str, Any]) -> dict[str, Any]:
    key = normalize_rate_limit_tool_key(str(tool.get("key") or ""))
    label = str(tool.get("label") or key).strip() or key
    description = str(tool.get("description") or "").strip()
    source = str(tool.get("source") or "unknown").strip() or "unknown"
    return {
        "key": key,
        "id": key,
        "name": key,
        "label": label,
        "description": description,
        "source": source,
        "label_key": get_rate_limit_tool_label_i18n_key(key),
        "description_key": get_rate_limit_tool_description_i18n_key(key),
        "available": bool(tool.get("available", True)),
    }


def _list_custom_tools(db) -> list[dict[str, Any]]:
    if db is None:
        return []
    try:
        from app.tools.custom.models import list_custom_python_tools
    except Exception:
        return []

    tools: list[dict[str, Any]] = []
    for tool in list_custom_python_tools(db, enabled_only=False):
        name = normalize_rate_limit_tool_key(getattr(tool, "name", ""))
        if not name:
            continue
        tools.append(
            {
                "key": name,
                "label": getattr(tool, "display_name", None) or name,
                "description": getattr(tool, "description", None) or "",
                "source": "custom_python",
                "available": bool(getattr(tool, "enabled", True)),
            }
        )
    return tools


def _build_mcp_public_name(server, tool_name: str) -> str:
    try:
        from app.mcp.utils import _build_public_tool_name

        return _build_public_tool_name(server, tool_name)
    except Exception:
        namespace = str(getattr(server, "namespace", None) or getattr(server, "name", None) or "mcp").strip()
        namespace = "".join(char.lower() if char.isalnum() else "_" for char in namespace).strip("_") or "mcp"
        tool_slug = "".join(char.lower() if char.isalnum() else "_" for char in tool_name).strip("_") or "tool"
        return f"mcp_{namespace}_{tool_slug}"[:64]


def _list_mcp_tools(db) -> list[dict[str, Any]]:
    if db is None:
        return []
    try:
        from app.mcp.models import MCPServer
    except Exception:
        return []

    tools: list[dict[str, Any]] = []
    servers = db.query(MCPServer).filter(MCPServer.enabled.is_(True)).all()
    for server in servers:
        status = getattr(server, "status", None)
        status = status if isinstance(status, dict) else {}
        tool_names = status.get("tool_names") or []
        if not isinstance(tool_names, list):
            continue
        for raw_tool_name in tool_names:
            tool_name = str(raw_tool_name or "").strip()
            if not tool_name:
                continue
            public_name = normalize_rate_limit_tool_key(_build_mcp_public_name(server, tool_name))
            tools.append(
                {
                    "key": public_name,
                    "label": f"{getattr(server, 'name', None) or 'MCP'}: {tool_name}",
                    "description": getattr(server, "description", None) or "",
                    "source": "mcp",
                }
            )
    return tools


def list_rate_limit_tools(db=None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    tools: list[dict[str, Any]] = []
    for raw_tool in [*BUILTIN_RATE_LIMIT_TOOLS, *_list_custom_tools(db), *_list_mcp_tools(db)]:
        tool = _decorate_tool(raw_tool)
        key = tool["key"]
        if not key or key in seen:
            continue
        seen.add(key)
        tools.append(tool)
    return sorted(tools, key=lambda item: (item["source"], item["label"].lower(), item["key"]))


def list_rate_limit_tool_keys(db=None) -> list[str]:
    return [tool["key"] for tool in list_rate_limit_tools(db)]


def get_rate_limit_tool(db, tool_key: str) -> dict[str, Any] | None:
    normalized = normalize_rate_limit_tool_key(tool_key)
    for tool in list_rate_limit_tools(db):
        if tool["key"] == normalized:
            return tool
    if normalized:
        return _decorate_tool({"key": normalized, "label": normalized, "description": "", "source": "unknown"})
    return None
