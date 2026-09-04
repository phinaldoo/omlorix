from copy import deepcopy

from app.tools.code_execution.utils import build_code_execution_tool_schema


_AUTOMATION_FIELD_SCHEMAS = {
    "automation_id": {"type": "string", "description": "ID of the automation."},
    "title": {"type": "string", "description": "Automation title."},
    "prompt": {"type": "string", "description": "Automation prompt content."},
    "model_id": {
        "type": "string",
        "description": "Internal model ID. Use an id returned by the information operation.",
    },
    "icon": {
        "anyOf": [{"type": "integer"}, {"type": "string"}],
        "description": "Optional integer icon picker number from the information operation. Existing stored icon strings are also accepted for compatibility.",
    },
    "icon_color": {
        "anyOf": [{"type": "integer"}, {"type": "string"}],
        "description": "Optional integer color picker number from the information operation. Existing hex colors are also accepted for compatibility.",
    },
    "schedule_rules": {
        "type": "array",
        "maxItems": 100,
        "description": "Optional scheduling rules.",
        "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["recurring", "once"]},
                "times": {"type": "array", "items": {"type": "string"}},
                "days": {"type": "array", "items": {"type": "integer"}},
                "run_at": {"type": "string"},
                "label": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "schedule_timezone": {
        "type": "string",
        "description": "Optional IANA timezone for recurring schedule rules (for example, America/New_York).",
    },
    "skill_id": {"type": "string", "description": "Optional linked skill ID."},
    "note_ids": {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string"},
        "description": "Optional linked note IDs.",
    },
    "file_ids": {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string"},
        "description": "Optional linked file IDs.",
    },
    "mcp_server_ids": {
        "type": "array",
        "maxItems": 100,
        "items": {"type": "string"},
        "description": "Optional MCP server IDs explicitly allowed for each automation run. Use only IDs returned for the selected model by the information operation.",
    },
    "is_active": {"type": "boolean", "description": "Optional active state."},
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "description": "Page size. Defaults to 20.",
    },
    "cursor": {"type": "string", "maxLength": 4096, "description": "Use next_cursor from the previous page. Omit offset when continuing."},
    "offset": {
        "type": "integer",
        "minimum": 0,
        "maximum": 10000,
        "description": "Page offset.",
    },
    "query": {
        "type": "string",
        "maxLength": 200,
        "description": "For view, return bounded prompt context around a match.",
    },
    "heading": {
        "type": "string",
        "maxLength": 500,
        "description": "For view, return one Markdown heading section from the prompt.",
    },
    "start_line": {
        "type": "integer",
        "minimum": 1,
        "description": "For view, first prompt line of a bounded range.",
    },
    "end_line": {
        "type": "integer",
        "minimum": 1,
        "description": "For view, final prompt line of a bounded range.",
    },
    "max_chars": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100000,
        "description": "Maximum prompt characters returned by view. Defaults to 20000.",
    },
}


def _automation_operation_schema(
    operation: str,
    fields: tuple[str, ...] = (),
    *,
    required: tuple[str, ...] = (),
) -> dict:
    """Build one exact Automations input shape without unrelated defaults."""
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [operation],
                "description": f"Run the {operation} operation.",
            },
            **{
                field: deepcopy(_AUTOMATION_FIELD_SCHEMAS[field])
                for field in fields
            },
        },
        "required": ["type", *required],
        "additionalProperties": False,
    }


_AUTOMATION_MUTATION_FIELDS = (
    "title",
    "prompt",
    "model_id",
    "icon",
    "icon_color",
    "schedule_rules",
    "schedule_timezone",
    "skill_id",
    "note_ids",
    "file_ids",
    "mcp_server_ids",
    "is_active",
)

_AUTOMATION_PARAMETERS = {
    "type": "object",
    "anyOf": [
        _automation_operation_schema("information", ("model_id",)),
        _automation_operation_schema("list", ("limit", "offset", "cursor")),
        _automation_operation_schema(
            "view",
            (
                "automation_id",
                "query",
                "heading",
                "start_line",
                "end_line",
                "max_chars",
            ),
            required=("automation_id",),
        ),
        _automation_operation_schema(
            "create",
            _AUTOMATION_MUTATION_FIELDS,
            required=("title", "prompt", "model_id"),
        ),
        _automation_operation_schema(
            "edit",
            ("automation_id", *_AUTOMATION_MUTATION_FIELDS),
            required=("automation_id",),
        ),
        _automation_operation_schema(
            "delete",
            ("automation_id",),
            required=("automation_id",),
        ),
    ],
}


# -------------------
# Tool Schemas
# -------------------
tool_schemas: dict[str, dict] = {
    "create_visualization": {
      "name": "create_visualization",
      "type": "function",
      "description": (
          "Create a polished inline visualization when seeing or interacting with the result materially improves the answer. "
          "Use Mermaid for simple static node-and-edge diagrams and Vega-Lite for ordinary declarative charts; use this tool "
          "for bespoke interactive explainers, simulations, maps, dense grids, timelines, comparisons, and UI mockups. "
          "Provide one self-contained HTML fragment under 1 MB with a stable root id. D3 v7, topojson, and lucide are bundled "
          "as globals. Do not use fetch, XMLHttpRequest, WebSocket, EventSource, external scripts, full HTML documents, "
          "document.currentScript, or window.openai. Use window.omlorix.visualization for approved external data or chat follow-ups."
      ),
      "parameters": {
          "type": "object",
          "properties": {
              "title": {
                  "type": "string",
                  "maxLength": 120,
                  "description": "Concise accessible title for the visualization.",
              },
              "mode": {
                  "type": "string",
                  "enum": ["normal", "wide"],
                  "description": "Use wide only for several compact panels that must remain side by side for comparison.",
              },
              "content": {
                  "type": "string",
                  "maxLength": 1048576,
                  "description": (
                      "Literal HTML fragment containing markup plus optional inline style and script. The first non-style/script "
                      "element must have a stable id. Use native controls, responsive sizing, theme variables, accessible names, "
                      "and a useful static first render before JavaScript enhancement."
                  ),
              },
              "capabilities": {
                  "type": "object",
                  "properties": {
                      "scripts": {
                          "type": "boolean",
                          "description": "Whether the viewer may enable authored JavaScript interactions.",
                      },
                      "external_data": {
                          "type": "boolean",
                          "description": "Whether the fragment may request public HTTP(S) data through the consent-gated host bridge.",
                      },
                      "chat_followup": {
                          "type": "boolean",
                          "description": "Whether labeled actions may propose a follow-up message to the current chat.",
                      },
                      "download": {
                          "type": "boolean",
                          "description": "Whether labeled actions may offer a small generated file for download after confirmation.",
                      },
                  },
                  "additionalProperties": False,
              },
          },
          "required": ["title", "content"],
          "additionalProperties": False,
      },
    },
    "subagent": {
      "name": "subagent",
      "type": "function",
      "description": (
          "Delegate work to another accessible chat model or saved Agent. "
          "Call action='list_targets' before delegation to search the user's authorized model and Agent targets. "
          "The legacy action='list_models' returns accessible models only. "
          "Use action='run' with either model_id and prompt, or agent_id and task. "
          "When the user selected explicit targets, discovery and run are restricted to that exact allowlist. "
          "Subagents receive the full parent chat context up to the call point. "
          "Saved Agents also receive their instructions, Skills, and reference assets. "
          "Targets use their saved tools, except subagent is removed to prevent recursive delegation."
      ),
      "parameters": {
          "type": "object",
          "properties": {
              "action": {
                  "type": "string",
                  "enum": ["list_targets", "list_models", "run"],
                  "description": "Use list_targets to search authorized models and Agents; list_models preserves legacy model-only discovery; run starts one subagent.",
              },
              "model_id": {
                  "type": "string",
                  "description": (
                      "Accessible model UUID for direct model action='run'. "
                      "Must be one of the model IDs returned by discovery. Required unless agent_id is used."
                  ),
              },
              "agent_id": {
                  "type": "string",
                  "description": "Accessible saved Agent ID for action='run'. Required unless model_id is used.",
              },
              "prompt": {
                  "type": "string",
                  "description": "Task prompt for direct model action='run'. Do not include a separate system prompt field.",
              },
              "task": {
                  "type": "string",
                  "description": "Task prompt for saved Agent action='run'.",
              },
              "context": {
                  "type": "string",
                  "description": "Optional extra context for action='run'. The full parent chat context is already included.",
              },
              "query": {
                  "type": "string",
                  "description": "Optional display-name, description, provider, or base-model search for action='list_targets'.",
              },
              "target_type": {
                  "type": "string",
                  "enum": ["all", "model", "agent"],
                  "description": "Optional target-kind filter for action='list_targets'. Defaults to all.",
              },
              "limit": {
                  "type": "integer",
                  "minimum": 1,
                  "maximum": 50,
                  "description": "Maximum targets returned by action='list_targets'.",
              },
              "cursor": {
                  "type": "string",
                  "description": "Opaque next_cursor from a previous action='list_targets' result.",
              },
          },
          "required": ["action"],
          "additionalProperties": False,
      },
    },
    "web_search": {
      "name": "web_search",
      "type": "function",
      "description": "Search the web by query, fetch direct URL content, or do both in one call.",
      "parameters": {
          "type": "object",
          "properties": {
              "queries": {
                  "type": "array",
                  "items": {"type": "string"},
                  "description": "Search queries (search mode). Maximum 3 queries; prefer as few as possible. May be combined with urls in the same call."
              },
              "urls": {
                  "type": "array",
                  "items": {"type": "string"},
                  "description": "Explicit URLs to fetch (direct URL mode). May be combined with queries in the same call."
              },
              "view_raw": {
                  "type": "boolean",
                  "description": "Return raw HTML where supported. Only valid when urls are provided."
              },
          },
          "additionalProperties": False,
      },
    },
    "weather": {
    "name": "weather",
    "type": "function",
    "description": "Get weather data of a given location. If there is no location given, it automatically chooses the location of the user. Pls only provide the city name, without country or any other information.",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "The location to get the weather data from."
            },
        },
        "required": [],
    },
  },
  "todos": {
    "name": "todos",
    "type": "function",
    "description": "Manage todos and todo lists with paginated summary lists and a detail view. Use type to choose list/view/create/edit/bulk and entity to choose todo or list. Todos and lists cannot be deleted with this tool. Supports filtered views, due dates, all-day tasks, notes, links, attachments, subtasks, tags, priorities, status, ordering, completion, and bulk updates.",
    "parameters": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["list", "view", "create", "edit", "bulk"],
                "description": "Operation to run.",
            },
            "entity": {
                "type": "string",
                "enum": ["todo", "list"],
                "description": "Target entity. Use 'todo' for individual items and 'list' for todo lists.",
            },
            "todo_list_id": {"type": "string", "description": "Todo list ID (required for todo list operations and listing/creating todos)."},
            "todo_id": {"type": "string", "description": "Todo ID (required for editing a todo)."},
            "title": {"type": "string", "description": "Todo list title (create/edit list)."},
            "description": {"type": "string", "description": "Todo list description (create/edit list)."},
            "icon": {"type": "string", "description": "Todo list icon (create/edit list)."},
            "share": {"type": "object", "description": "Todo list share config (create/edit list)."},
            "sort_order": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "direction": {"type": "string"},
                    },
                },
                "description": "Todo list sort order (create/edit list).",
            },
            "content": {"type": "string", "description": "Todo content (create/edit todo)."},
            "notes": {"type": "string", "description": "Todo notes (create/edit todo)."},
            "priority": {"type": "integer", "description": "Todo priority (create/edit todo)."},
            "due_at": {"type": "string", "description": "ISO datetime due date for todo (create/edit todo)."},
            "clear_due_at": {"type": "boolean", "description": "Set true to remove a todo due date."},
            "all_day": {"type": "boolean", "description": "Whether the due date is an all-day task."},
            "status": {"type": "string", "enum": ["todo", "doing", "done"], "description": "Kanban status for a todo."},
            "subtasks": {"type": "array", "items": {"type": "object"}, "description": "Checklist subtasks for a todo, for example objects with title and is_done."},
            "links": {"type": "array", "items": {"type": "object"}, "description": "Links attached to the todo, for example objects with title and url."},
            "attachments": {"type": "array", "items": {"type": "object"}, "description": "Attachment metadata for a todo."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to set or add."},
            "order": {"type": "integer", "description": "Ordering index for todo or list."},
            "is_done": {"type": "boolean", "description": "Completion state (edit todo)."},
            "is_marked": {"type": "boolean", "description": "Marked/starred state (edit todo)."},
            "query": {"type": "string", "maxLength": 200, "description": "Search text for listing todos across accessible lists."},
            "view": {"type": "string", "enum": ["today", "upcoming", "overdue", "due_this_week", "high_priority", "no_due_date"], "description": "Filtered todo view to list."},
            "priority_min": {"type": "integer", "description": "Minimum priority filter for listing todos."},
            "no_due_date": {"type": "boolean", "description": "Filter todos with no due date."},
            "todo_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100, "description": "Todo IDs for bulk actions."},
            "action": {"type": "string", "enum": ["complete", "incomplete", "move", "tag"], "description": "Non-destructive bulk action to apply."},
            "target_list_id": {"type": "string", "description": "Destination list for bulk move."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Page size for list. Defaults to 20."},
            "cursor": {"type": "string", "maxLength": 4096, "description": "Use next_cursor from the previous page. Omit offset when continuing."},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "description": "Page offset for list."},
        },
        "required": ["type"],
    },
  },
  "notes": {
    "name": "notes",
    "type": "function",
    "description": "Manage notes with bounded, revision-aware operations. Lists contain summaries only. Use view or view_many for bounded content reads. For multiple changes, send one atomic edits array and expected_updated_at instead of making several edit calls. A current attached-note snapshot or save receipt provides a valid expected_updated_at.",
    "parameters": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["list", "view", "view_many", "create", "edit"],
                "description": "Operation to run.",
            },
            "note_id": {"type": "string", "description": "Note ID for view/edit."},
            "note_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
                "description": "Note IDs for one bounded view_many call.",
            },
            "expected_updated_at": {
                "type": "string",
                "description": "Required for edit. Copy the exact updated_at value from the latest view result so stale mutations are rejected.",
            },
            "start_snippet": {
                "type": "string",
                "description": "For partial edits only. Exact unique snippet where the replacement range starts. Requires note_id, end_snippet, and content. The matched start snippet is replaced too.",
            },
            "end_snippet": {
                "type": "string",
                "description": "For partial edits only. Exact snippet where the replacement range ends, searched after start_snippet. Requires note_id, start_snippet, and content. The matched end snippet is replaced too.",
            },
            "edits": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "description": "Atomic non-overlapping replacements resolved against one note snapshot.",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_snippet": {"type": "string", "minLength": 1},
                        "end_snippet": {"type": "string", "minLength": 1},
                        "content": {"type": "string"},
                    },
                    "required": ["start_snippet", "end_snippet", "content"],
                    "additionalProperties": False,
                },
            },
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "Search note summaries for list, or return context around a match for view/view_many.",
            },
            "heading": {"type": "string", "maxLength": 500, "description": "Return one Markdown heading section for view/view_many."},
            "start_line": {"type": "integer", "minimum": 1, "description": "First line for a bounded read."},
            "end_line": {"type": "integer", "minimum": 1, "description": "Final line for a bounded read."},
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000,
                "description": "Maximum characters returned per note read. Defaults to 20000.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Summary page size for list. Defaults to 20."},
            "cursor": {"type": "string", "maxLength": 4096, "description": "Use next_cursor from the previous page. Omit offset when continuing."},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "description": "Summary page offset for list."},
            "content": {"type": "string", "description": "Note content for create/full edit, or replacement content when start_snippet and end_snippet are provided. Use an empty string to delete the matched snippet range."},
        },
        "required": ["type"],
    },
  },
  "automations": {
    "name": "automations",
    "type": "function",
    "description": "Manage scheduled automations with paginated summaries and a detail view. Call information only when create/edit needs valid model, Skill, icon, color, schedule, or model-specific MCP options; list, view, and delete do not need it first. Webhook triggers are user-managed and this tool cannot create, change, rotate, or delete them.",
    "parameters": _AUTOMATION_PARAMETERS,
  },
  "skills": {
    "name": "skills",
    "type": "function",
    "description": "Manage user skills with paginated summary lists and bounded targeted reads, or propose a new skill draft for manual user review and save confirmation.",
    "parameters": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["list", "read", "draft"],
                "description": "Operation to run.",
            },
            "skill_id": {
                "type": "string",
                "description": "Skill ID. Required for read.",
            },
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "Search names/descriptions for list, or return context around a match for read.",
            },
            "heading": {
                "type": "string",
                "maxLength": 500,
                "description": "For read, return one Markdown heading section.",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "For read, first line of a bounded range.",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "For read, final line of a bounded range.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000,
                "description": "Maximum characters returned by read. Defaults to 20000.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Summary page size for list. Defaults to 20.",
            },
            "cursor": {"type": "string", "maxLength": 4096, "description": "Use next_cursor from the previous page. Omit offset when continuing."},
            "offset": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10000,
                "description": "Summary page offset for list.",
            },
            "name": {
                "type": "string",
                "description": "Skill slug for draft mode. Use lowercase letters, numbers, and hyphens.",
            },
            "description": {
                "type": "string",
                "description": "Short description for draft mode.",
            },
            "content": {
                "type": "string",
                "description": "Body instructions that will become the SKILL.md body in draft mode.",
            },
            "icon": {
                "type": "string",
                "description": "Optional preset icon ID or icon JSON for draft mode.",
            },
            "compatibility": {
                "type": "string",
                "description": "Optional compatibility field for draft mode.",
            },
            "license_value": {
                "type": "string",
                "description": "Optional license field for draft mode.",
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata object for draft mode.",
                "additionalProperties": {
                    "type": ["string", "number", "null"],
                },
            },
            "files": {
                "type": "array",
                "maxItems": 20,
                "description": "Optional starter files for draft mode. Inline text files can set content. Existing generated chat files can be attached with source_file_id.",
                "items": {
                    "type": "object",
                    "properties": {
                        "folder_type": {
                            "type": "string",
                            "enum": ["scripts", "references", "assets"],
                        },
                        "filename": {
                            "type": "string",
                        },
                        "content": {
                            "type": "string",
                            "description": "Inline UTF-8 text content for the file. Omit when using source_file_id.",
                        },
                        "encoding": {
                            "type": "string",
                            "enum": ["utf-8", "base64"],
                            "description": "Encoding for content. Prefer utf-8; base64 is only for text that should decode to UTF-8.",
                        },
                        "source_file_id": {
                            "type": "string",
                            "description": "Existing user-owned chat file ID to copy into the skill during save.",
                        },
                        "media_type": {
                            "type": "string",
                            "description": "Optional media type hint such as text/markdown or image/svg+xml.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional short note shown in the draft widget.",
                        },
                    },
                    "required": ["folder_type", "filename"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["type"],
        "additionalProperties": False,
    },
  },
    "quiz": {
        "name": "quiz",
        "type": "function",
        "description": "Create an interactive quiz widget. Each question must include exactly 4 answer options and one correct option index.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Quiz title shown in the widget.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional short subtitle or instructions for the quiz.",
                },
                "questions": {
                    "type": "array",
                    "description": "Quiz questions. Each question must have 4 options and one correct option index.",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The question text.",
                            },
                            "options": {
                                "type": "array",
                                "description": "Exactly 4 answer options.",
                                "items": {"type": "string"},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            "correct_option_index": {
                                "type": "integer",
                                "description": "0-based index of the correct option (0 to 3).",
                                "minimum": 0,
                                "maximum": 3,
                            },
                            "explanation": {
                                "type": "string",
                                "description": "Optional explanation shown in the final result details.",
                            },
                        },
                        "required": ["question", "options", "correct_option_index"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "questions"],
            "additionalProperties": False,
        },
    },
    "flashcards": {
        "name": "flashcards",
        "type": "function",
        "description": "Create an interactive flashcards widget for studying vocabulary, concepts, definitions, or question/answer pairs.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Deck title shown in the widget.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional short study goal or instructions for the deck.",
                },
                "cards": {
                    "type": "array",
                    "description": "Flashcards to study. Prefer front/back wording, but term/definition also works.",
                    "minItems": 1,
                    "maxItems": 40,
                    "items": {
                        "type": "object",
                        "properties": {
                            "front": {
                                "type": "string",
                                "description": "Text shown before flipping the card.",
                            },
                            "back": {
                                "type": "string",
                                "description": "Text revealed after flipping the card.",
                            },
                            "hint": {
                                "type": "string",
                                "description": "Optional clue or memory cue.",
                            },
                            "example": {
                                "type": "string",
                                "description": "Optional usage example or contextual sentence.",
                            },
                            "pronunciation": {
                                "type": "string",
                                "description": "Optional pronunciation guidance, useful for vocabulary.",
                            },
                            "category": {
                                "type": "string",
                                "description": "Optional topic label, language, or grouping tag.",
                            },
                            "note": {
                                "type": "string",
                                "description": "Optional learning note or mnemonic.",
                            },
                        },
                        "required": ["front", "back"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "cards"],
            "additionalProperties": False,
        },
    },
    "image_generation": {
        "name": "image_generation",
        "type": "function",
        "description": (
            "This function generates an image from a detailed description. "
            "When image-edit mode is enabled in admin settings, it can also edit "
            "images using chat images as references."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "The description of the image."},
            },
            "required": ["description"],
        },
    },
    "video_generation": {
        "name": "video_generation",
        "type": "function",
        "description": (
            "Generate a short video from a text description using the configured provider/model. "
            "When enabled in admin settings, this tool can also use reference files from chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Detailed prompt describing the video scene and motion."},
            },
            "required": ["description"],
        },
    },
    "audio_generation": {
        "name": "audio_generation",
        "type": "function",
        "description": "Generate a speech file from a given text (TTS).",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "The text which will be converted into audio."},
                "instructions": {"type": "string", "description": "Optionally, a detailed description on how the voice should sound and behave. Its recommended to use."},
            },
            "required": ["input"],
        },
    },
    "music_generation": {
        "name": "music_generation",
        "type": "function",
        "description": (
            "Generate music from a detailed prompt using the configured provider/model. "
            "Optionally provide custom lyrics, and when enabled in admin settings it can "
            "also use recent chat images as visual inspiration."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Detailed prompt describing genre, mood, instrumentation, arrangement, vocals, and production style.",
                },
                "lyrics": {
                    "type": "string",
                    "description": "Optional custom lyrics to include or strongly steer vocal content.",
                },
            },
            "required": ["description"],
        },
    },
    "slide_presentation": {
        "name": "slide_presentation",
        "type": "function",
        "description": "Create a polished slide presentation directly from one Markdown brief. The Markdown file must contain all content, requirements, and source notes needed for the deck. The tool creates, renders, visually reviews, and refines the final HTML automatically.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Required file ID of an owned Markdown file containing the complete presentation brief and source material.",
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": "Optional owned image file IDs that the presentation must use. The images are shown to the presentation model and embedded into the final HTML and PowerPoint.",
                },
            },
            "required": ["file_id"],
        },
    },
    "deep_research": {
        "name": "deep_research",
        "type": "function",
        "description": (
            "Run a deep, multi-step research process with the configured provider. "
            "Use this for comprehensive topics that require broad source coverage and synthesis. "
            "The result is a canonical Markdown report. If the user also wants an HTML page, "
            "use the Canvas tool after Deep Research completes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question or task to investigate in depth.",
                },
            },
            "required": ["query"],
        },
    },
    "deep_research_import_web_image": {
        "name": "deep_research_import_web_image",
        "type": "function",
        "description": (
            "Deep Research only: securely copy an evidence-bearing raster image found "
            "through web_search into the report workspace. Supply the image URL, its "
            "source page, attribution, and useful alt text. Do not use decorative images."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": "Direct public raster image URL returned by web_search.",
                },
                "source_url": {
                    "type": "string",
                    "description": "Public page URL that establishes provenance.",
                },
                "attribution": {
                    "type": "string",
                    "description": "Creator, publisher, or source attribution.",
                },
                "alt_text": {
                    "type": "string",
                    "description": "Concise description of the image and its evidentiary relevance.",
                },
                "caption": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Evidence-focused caption stating why this image matters.",
                },
                "license_name": {
                    "type": "string",
                    "description": "Optional verified license; omit when not verified.",
                },
            },
            "required": ["image_url", "source_url", "attribution", "alt_text", "caption"],
            "additionalProperties": False,
        },
    },
    "canvas": {
        "name": "canvas",
        "type": "function",
        "description": "Create, view, or update an editable Canvas source file. Supports markdown, Mermaid diagrams, CSV tables, HTML pages, and complete LaTeX documents with a generated PDF preview. Reads are bounded and can target a heading, query, or line range. For multiple revisions, send one atomic edits array with file_id and expected_revision instead of making one tool call per change.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["markdown", "mermaid", "csv", "html", "latex", "view"],
                    "description": "Operation/content type: use 'view' to load current stored content; 'markdown' for rich text, 'mermaid' for diagram source, 'csv' for tabular data, 'html' for a complete page, or 'latex' for a complete compilable .tex document whose PDF is rendered in the Canvas preview. Defaults to markdown.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename (e.g. notes.md, diagram.mmd, data.csv, website.html, report.tex). Extension is added from the selected type when missing.",
                },
                "file_id": {
                    "type": "string",
                    "description": "Existing canvas file id to view or update. Required for type='view' and partial edits.",
                },
                "id": {
                    "type": "string",
                    "description": "Alias for file_id when type='view'.",
                },
                "start_snippet": {
                    "type": "string",
                    "description": "For partial edits only. Exact unique snippet where the replacement range starts. Requires file_id, end_snippet, and content. The matched start snippet is replaced too.",
                },
                "end_snippet": {
                    "type": "string",
                    "description": "For partial edits only. Exact snippet where the replacement range ends, searched after start_snippet. Requires file_id, start_snippet, and content. The matched end snippet is replaced too.",
                },
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Required for safe updates of an existing Canvas. Copy canvas_revision from the latest view or save receipt.",
                },
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "description": "Atomic non-overlapping replacements resolved against one stored snapshot. Use this instead of several Canvas calls.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_snippet": {"type": "string", "minLength": 1},
                            "end_snippet": {"type": "string", "minLength": 1},
                            "content": {"type": "string"},
                        },
                        "required": ["start_snippet", "end_snippet", "content"],
                        "additionalProperties": False,
                    },
                },
                "heading": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "For type='view', return the matching Markdown heading section.",
                },
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "For type='view', return bounded context around the first case-insensitive match.",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "For type='view', first line of a bounded line range.",
                },
                "end_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "For type='view', final line of a bounded line range.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100000,
                    "description": "Maximum text returned by a Canvas view. Defaults to 20000 characters.",
                },
                "content": {
                    "type": "string",
                    "description": "Required unless type='view'. Full file content for create/full overwrite, or replacement content when start_snippet and end_snippet are provided. Use an empty string to delete the matched snippet range. Complete HTML pages may include JavaScript, event handlers, forms, embedded media, and external resources. Keep dependencies minimal and prefer self-contained HTML; scripts execute inside an isolated Canvas preview, while remote resources and network requests require an explicit viewer grant. For type='latex', generate portable pdflatex source and do not add babel/polyglossia or locale-specific packages merely because of the conversation language. If a validation error says retry_allowed=true, correct the existing payload and retry once; if false, do not call another tool in this response.",
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": "For type='latex', optional user file IDs made available to the renderer under their original filenames. When editing a slide-presentation HTML Canvas, pass every newly referenced image ID and use src='omlorix-file://FILE_ID'; Omlorix securely embeds those images before rendering. Omit during edits to preserve the existing asset bundle; pass [] to clear it.",
                },
            },
            "required": [],
        },
    },
    "code_execution": build_code_execution_tool_schema(
        default_type="public",
        description=(
            "Execute code inside a secure persistent container sandbox (python or bash). "
            "Containers are chat-scoped and reused across tool calls while active. "
            "Set type to 'public' to expose results to the user, or 'internal' for model-only reasoning. "
            "Files generated under /tmp/output/ are returned to the assistant and saved into chat files."
        ),
    ),
}
