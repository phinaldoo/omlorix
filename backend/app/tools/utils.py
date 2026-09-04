from collections import deque
from copy import deepcopy
from typing import Any
import json
import logging

from app.groups.init import get_user_group_setting_value
from app.tools.weather.utils import get_weather
from app.tools.image_generation.utils import image_generation
from app.tools.video_generation.utils import video_generation
from app.tools.audio_generation.utils import audio_generation
from app.tools.music_generation.utils import music_generation
# ``view_canvas_file`` remains an intentional re-export consumed by
# ``app.tools.helper`` alongside the registry helpers in this module.
from app.tools.canvas_markdown.utils import save_canvas_markdown, view_canvas_file  # noqa: F401
from app.tools.flashcards.utils import create_flashcards
from app.tools.quiz.utils import create_quiz
from app.tools.deep_research.utils import deep_research
from app.tools.code_execution.utils import (
  build_code_execution_tool_schema,
  code_execution_supports_external_pip_packages,
)
from app.tools.schemas import tool_schemas
from app.tools.todos.utils import TODO_TOOL_OPERATIONS, todos_tool
from app.tools.notes.utils import notes_tool
from app.tools.automations.utils import WEBHOOK_MANAGEMENT_USER_MESSAGE, automations_tool
from app.tools.skills.utils import skills_tool
from app.tools.websearch.utils import web_search
from app.tools.custom.utils import list_enabled_custom_python_tool_names, list_enabled_custom_python_tool_options


logger = logging.getLogger(__name__)



# -------------------
# Available Tools
# -------------------
available_tools = {
    "create_visualization": None,
    "subagent": None,
    'web_search': web_search,
    "weather": get_weather,
    "flashcards": create_flashcards,
    "quiz": create_quiz,
    "todos": todos_tool,
    "notes": notes_tool,
    "automations": automations_tool,
    "skills": skills_tool,
    "image_generation": image_generation,
    "video_generation": video_generation,
    "audio_generation": audio_generation,
    "music_generation": music_generation,
    "canvas": save_canvas_markdown,
    "slide_presentation": None,
    "deep_research": deep_research,
    "deep_research_import_web_image": None,
    "code_execution": None,
}



# -------------------
# List Available Tool Names
# -------------------
def list_available_tool_names(db) -> list[str]:
    """Return a list of all available tool names, including custom tools."""
    names = list(available_tools.keys())
    if db is None:
        return names
    for custom_name in list_enabled_custom_python_tool_names(db):
        if custom_name not in names:
            names.append(custom_name)
    return names



# -------------------
# List Available Tool Options
# -------------------
def list_available_tool_options(db) -> list[dict[str, Any]]:
    from app.tools.registry import get_rate_limit_tool_label_i18n_key

    options = [
        {
            "name": name,
            "label": name.replace("_", " ").title(),
            "i18n_label": get_rate_limit_tool_label_i18n_key(name),
        }
        for name in available_tools.keys()
        if name != "deep_research_import_web_image"
    ]
    if db is None:
        return options
    existing_names = {item["name"] for item in options if item.get("name")}
    for item in list_enabled_custom_python_tool_options(db):
        name = item.get("name")
        if not name or name in existing_names:
            continue
        options.append(
            {
                "name": name,
                "label": item.get("label") or name,
            }
        )
        existing_names.add(name)
    return options



def _resolve_web_search_provider_id(
    model_settings: dict[str, Any] | None = None,
    *,
    db=None,
    user_id: str | None = None,
    byok: dict | None = None,
) -> str | None:
  if byok:
    if db is None or not user_id:
      return None
    try:

      provider_id = get_user_group_setting_value(user_id, "chat", "byok_default_search_provider", db)
    except Exception:
      return None
    if provider_id is None:
      return None
    return str(provider_id).strip() or None

  if not isinstance(model_settings, dict):
    return None

  provider_id = model_settings.get("websearch_search_provider")
  if provider_id is None:
    return None
  return str(provider_id).strip() or None


def _web_search_supports_image_mode(
    *,
    db=None,
    model_settings: dict[str, Any] | None = None,
    user_id: str | None = None,
    byok: dict | None = None,
) -> bool:
  provider_id = _resolve_web_search_provider_id(
    model_settings,
    db=db,
    user_id=user_id,
    byok=byok,
  )
  if not provider_id:
    return False
  if provider_id.lower() == "searxng":
    return True
  if db is None:
    return False

  try:
    from app.tools.websearch.models import WebSearchProvider

    provider = (
      db.query(WebSearchProvider)
      .filter(WebSearchProvider.id == provider_id)
      .first()
    )
  except Exception:
    return False

  if not provider or getattr(provider, "provider", None) != "searxng":
    return False

  provider_types = provider.type or []
  return "search" in provider_types or "combined" in provider_types


def _get_web_search_tool_schema(
    *,
    db=None,
    model_settings: dict[str, Any] | None = None,
    user_id: str | None = None,
    byok: dict | None = None,
) -> dict:
  resolved_spec = deepcopy(tool_schemas["web_search"])
  if not _web_search_supports_image_mode(
    db=db,
    model_settings=model_settings,
    user_id=user_id,
    byok=byok,
  ):
    return resolved_spec

  resolved_spec["description"] = (
    "Search the web by query, search images via SearXNG, fetch direct URL content, or combine queries and urls in one call."
  )
  parameters = resolved_spec.get("parameters")
  properties = parameters.get("properties") if isinstance(parameters, dict) else None
  if isinstance(properties, dict):
    properties["search_mode"] = {
      "type": "string",
      "enum": ["web", "images"],
      "description": "Optional. Use 'images' to return image metadata via SearXNG. Defaults to 'web'. Only valid when queries are provided.",
    }
    properties["limit"] = {
      "type": "integer",
      "description": "Optional number of images to return (1-10, default 5). Only valid when search_mode is 'images'.",
    }

  return resolved_spec


# -------------------
# Get Tool Schemas
# -------------------
def get_tool_schemas(
  enabled_names: list[str] | None = None,
  db=None,
  model_settings: dict[str, Any] | None = None,
  user_id: str | None = None,
  byok: dict | None = None,
  project_id: str | None = None,
) -> list[dict]:
  """Return list of tool schema dicts for the given tool names.

  If enabled_names is None, returns schemas for all tools in available_tools order.
  Unknown names are ignored.
  """

  raw_names = enabled_names if enabled_names is not None else list_available_tool_names(db=db)
  names: list[str] = []
  seen: set[str] = set()
  code_execution_default_type = "public"
  explicit_code_execution_requested = False

  tool_name_aliases = {
    "get_weather": "weather",
    "create_flashcards": "flashcards",
    "create_quiz": "quiz",
    # Existing model settings should transparently receive the unified Canvas
    # tool after the dedicated model-facing LaTeX tool is retired.
    "latex_pdf": "canvas",
  }

  for raw_name in raw_names:
    if raw_name == "code_execution_internal":
      code_execution_default_type = "internal" if not explicit_code_execution_requested else code_execution_default_type
      normalized_name = "code_execution"
    else:
      normalized_name = tool_name_aliases.get(raw_name, raw_name)
      if normalized_name == "code_execution":
        explicit_code_execution_requested = True
        code_execution_default_type = "public"

    if normalized_name not in seen:
      seen.add(normalized_name)
      names.append(normalized_name)

  specs: list[dict] = []
  for n in names:
    if n == "web_search":
      resolved_spec = _get_web_search_tool_schema(
        db=db,
        model_settings=model_settings,
        user_id=user_id,
        byok=byok,
      )
    elif n == "code_execution":
      resolved_spec = build_code_execution_tool_schema(
        default_type=code_execution_default_type,
        allow_pip_packages=code_execution_supports_external_pip_packages(db),
        description=(
          "Execute code inside a secure persistent container sandbox (python or bash). "
          "Containers are chat-scoped and reused across tool calls while active. "
          "Set type to 'public' to expose results to the user, or 'internal' for model-only reasoning. "
          "Files generated under /tmp/output/ are returned to the assistant and saved into chat files."
        ),
      )
    else:
      spec = tool_schemas.get(n)
      if spec:
        resolved_spec = deepcopy(spec)
      else:
        resolved_spec = None
        try:
          if db is not None:
            from app.tools.custom.utils import get_enabled_custom_python_tool_schema

            resolved_spec = get_enabled_custom_python_tool_schema(db, n)
        except Exception:
          resolved_spec = None
      if not resolved_spec:
        continue
    if n == "audio_generation":
      try:
        from app.tools.audio_generation.utils import (
          audio_generation_supports_instructions,
          audio_generation_supports_multi_speakers,
        )

        supports_instructions = audio_generation_supports_instructions(db=db)
        supports_multi_speakers = audio_generation_supports_multi_speakers(db=db)
      except Exception:
        supports_instructions = True
        supports_multi_speakers = False

      parameters = resolved_spec.get("parameters")
      properties = None
      if isinstance(parameters, dict):
        maybe_properties = parameters.get("properties")
        if isinstance(maybe_properties, dict):
          properties = maybe_properties

      if properties is not None:
        if supports_multi_speakers:
          resolved_spec["description"] = (
            "Generate speech audio from text. Supports single-speaker narration and "
            "two-speaker dialogue when multiple_speakers is true."
          )
          input_property = properties.get("input")
          if isinstance(input_property, dict):
            input_property["description"] = (
              "Text to convert into speech. If multiple_speakers=true, format input as dialogue lines "
              "with speaker labels in this exact pattern: 'SpeakerName: line'. Use exactly 2 unique "
              "speaker names across the script."
            )
          instructions_property = properties.get("instructions")
          if isinstance(instructions_property, dict):
            instructions_property["description"] = (
              "Optional voice and style guidance (e.g., tone, pace, emotion). For dialogue, apply global "
              "direction only and keep speaker labels in input unchanged."
            )
          properties["multiple_speakers"] = {
            "type": "boolean",
            "description": (
              "Set true for two-speaker dialogue synthesis. Requires input lines with speaker labels "
              "like 'Alice: ...' and 'Bob: ...'."
            ),
          }
        else:
          properties.pop("multiple_speakers", None)

      if not supports_instructions:
        if properties is not None:
          properties.pop("instructions", None)
        if isinstance(parameters, dict):
          required = parameters.get("required")
          if isinstance(required, list):
            parameters["required"] = [item for item in required if item != "instructions"]

    if n == "image_generation":
      try:
        from app.tools.image_generation.utils import (
          get_image_edit_tool_params,
          get_image_size_tool_params,
        )
        size_params = get_image_size_tool_params(db=db)
        edit_params = get_image_edit_tool_params(db=db)
      except Exception:
        size_params = {}
        edit_params = {}

      if size_params or edit_params:
        parameters = resolved_spec.get("parameters")
        if isinstance(parameters, dict):
          properties = parameters.get("properties")
          if isinstance(properties, dict):
              properties.update(size_params)
              properties.update(edit_params)

    if n == "video_generation":
      try:
        from app.tools.video_generation.utils import get_video_reference_tool_params
        reference_params = get_video_reference_tool_params(db=db)
      except Exception:
        reference_params = {}

      if reference_params:
        parameters = resolved_spec.get("parameters")
        if isinstance(parameters, dict):
          properties = parameters.get("properties")
          if isinstance(properties, dict):
            properties.update(reference_params)

    if n == "music_generation":
      try:
        from app.tools.music_generation.utils import get_music_reference_tool_params
        reference_params = get_music_reference_tool_params(db=db)
      except Exception:
        reference_params = {}

      if reference_params:
        parameters = resolved_spec.get("parameters")
        if isinstance(parameters, dict):
          properties = parameters.get("properties")
          if isinstance(properties, dict):
            properties.update(reference_params)

    specs.append(resolved_spec)
  return specs



# -------------------
# Resolve Enabled Tools
# -------------------
def resolve_enabled_tools(
  raw_tools: Any,
  db=None,
  model_settings: dict[str, Any] | None = None,
  user_id: str | None = None,
  byok: dict | None = None,
  project_id: str | None = None,
) -> dict:
  """Normalize a raw tools payload into enabled names and schemas."""

  available_tool_name_set = set(list_available_tool_names(db=db))

  if raw_tools is None:
    raw_tools = []

  if isinstance(raw_tools, str):
    try:
      raw_tools = json.loads(raw_tools)
    except Exception:
      # keep original string (single tool name)
      pass

  queue = deque([raw_tools])
  names: list[str] = []
  seen: set[str] = set()
  mcp_requested = False
  legacy_aliases = {
    "list_todo_lists": "todos",
    "view_todo_list": "todos",
    "toggle_todo": "todos",
    "create_todo": "todos",
    "create_todo_list": "todos",
    "list_notes": "notes",
    "create_note": "notes",
    "edit_note": "notes",
    "code_execution_internal": "code_execution",
    "slide_presentation_legacy": "slide_presentation",
    "get_weather": "weather",
    "create_flashcards": "flashcards",
    "create_quiz": "quiz",
    "latex_pdf": "canvas",
  }

  byok_allowed_names: set[str] | None = None
  byok_mcp_allowed = True
  if byok:
    byok_allowed_names = set()
    byok_mcp_allowed = False
    raw_allowed_tools: Any = None
    if db is not None and user_id:
      try:

        raw_allowed_tools = get_user_group_setting_value(user_id, "chat", "byok_allowed_tools", db)
      except Exception:
        raw_allowed_tools = None

    if isinstance(raw_allowed_tools, str):
      try:
        raw_allowed_tools = json.loads(raw_allowed_tools)
      except Exception:
        raw_allowed_tools = [raw_allowed_tools]

    allowed_queue = deque([raw_allowed_tools])
    while allowed_queue:
      allowed_item = allowed_queue.popleft()
      allowed_candidate = None

      if isinstance(allowed_item, str):
        allowed_candidate = allowed_item
      elif isinstance(allowed_item, dict):
        allowed_name = allowed_item.get("name")
        if isinstance(allowed_name, str):
          allowed_candidate = allowed_name
        nested_allowed = allowed_item.get("tools")
        if nested_allowed is not None:
          if isinstance(nested_allowed, (list, tuple, set)):
            allowed_queue.extend(nested_allowed)
          else:
            allowed_queue.append(nested_allowed)
      elif isinstance(allowed_item, (list, tuple, set)):
        allowed_queue.extend(allowed_item)
        continue

      if not isinstance(allowed_candidate, str):
        continue

      normalized_allowed = legacy_aliases.get(allowed_candidate, allowed_candidate)
      if normalized_allowed == "mcp":
        byok_mcp_allowed = True
        continue
      if normalized_allowed in available_tool_name_set:
        byok_allowed_names.add(normalized_allowed)

  while queue:
    candidate = None
    item = queue.popleft()

    if isinstance(item, str):
      candidate = item
    elif isinstance(item, dict):
      value = item.get("name")
      if isinstance(value, str):
        candidate = value
      nested = item.get("tools")
      if nested is not None:
        if isinstance(nested, (list, tuple, set)):
          queue.extend(nested)
        else:
          queue.append(nested)
    elif isinstance(item, (list, tuple, set)):
      queue.extend(item)
      continue
    else:
      continue

    if isinstance(candidate, str):
      normalized_candidate = legacy_aliases.get(candidate, candidate)
      if normalized_candidate == "mcp":
        if byok and not byok_mcp_allowed:
          continue
        mcp_requested = True
        continue
      if byok and byok_allowed_names is not None and normalized_candidate not in byok_allowed_names:
        continue
      if normalized_candidate in available_tool_name_set and normalized_candidate not in seen:
        seen.add(normalized_candidate)
        names.append(normalized_candidate)

  tool_schemas = get_tool_schemas(
      names,
      db=db,
      model_settings=model_settings,
      user_id=user_id,
      byok=byok,
      project_id=project_id,
    )

  exposed_names: set[str] = set()
  for schema in tool_schemas:
    if isinstance(schema, dict):
      schema_name = schema.get("name")
      if isinstance(schema_name, str) and schema_name:
        exposed_names.add(schema_name)
  if exposed_names:
    names = [name for name in names if name in exposed_names]
  else:
    names = []

  return {
    "tool_list": names,
    "tool_schemas": tool_schemas,
    "mcp_requested": mcp_requested,
  }
