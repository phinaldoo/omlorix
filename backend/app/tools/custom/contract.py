from __future__ import annotations

from typing import Any
import json
import re


TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
SUPPORTED_SCHEMA_KEYS = {
    "type",
    "description",
    "enum",
    "items",
    "properties",
    "required",
    "additionalProperties",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
}
SUPPORTED_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
OUTPUT_KEYS = {
    "content",
    "result",
    "documents",
    "images",
    "videos",
    "audios",
    "youtube",
    "webpages",
    "tool_meta",
    "meta",
    "stream_events",
    "widget",
    "file_id",
}
WIDGET_RENDER_MODES = {"inline", "iframe"}


class CustomPythonToolContractError(ValueError):
    """Report violations in the custom Python tool definition or output contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def _json_path(path: str, key: str) -> str:
    """Append a child schema key to a dotted JSON-path-like error location."""

    if not path:
        return key
    if key.startswith("["):
        return f"{path}{key}"
    return f"{path}.{key}"


def _argument_validation_error(
    message: str,
    *,
    path: str,
    code: str = "custom_tool_argument_invalid",
) -> CustomPythonToolContractError:
    """Attach stable UI metadata to a runtime argument validation failure."""

    return CustomPythonToolContractError(message, code=code, path=path)


def _ensure_supported_schema_keys(schema: dict[str, Any], *, path: str) -> None:
    """Reject JSON Schema keywords that the custom tool runtime does not support."""

    unsupported = [key for key in schema.keys() if key not in SUPPORTED_SCHEMA_KEYS]
    if unsupported:
        joined = ", ".join(sorted(unsupported))
        raise CustomPythonToolContractError(
            f"Unsupported schema keyword(s) at {path or 'parameters'}: {joined}."
        )


def _validate_property_schema(schema: Any, *, path: str) -> dict[str, Any]:
    """Recursively validate and normalize the supported subset of tool parameter schema."""

    if not isinstance(schema, dict):
        raise CustomPythonToolContractError(f"Schema at {path} must be an object.")

    _ensure_supported_schema_keys(schema, path=path)

    schema_type = schema.get("type")
    if schema_type not in SUPPORTED_TYPES:
        supported = ", ".join(sorted(SUPPORTED_TYPES))
        raise CustomPythonToolContractError(
            f"Schema at {path} must declare a supported type ({supported})."
        )

    normalized = dict(schema)

    description = normalized.get("description")
    if description is not None and not isinstance(description, str):
        raise CustomPythonToolContractError(f"description at {path} must be a string.")

    enum_values = normalized.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, list) or not enum_values:
            raise CustomPythonToolContractError(f"enum at {path} must be a non-empty list.")

    if schema_type == "object":
        properties = normalized.get("properties", {})
        if not isinstance(properties, dict):
            raise CustomPythonToolContractError(f"properties at {path} must be an object.")
        normalized["properties"] = {
            key: _validate_property_schema(value, path=_json_path(path, key))
            for key, value in properties.items()
        }
        required = normalized.get("required", [])
        if required is None:
            required = []
        if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
            raise CustomPythonToolContractError(f"required at {path} must be a list of property names.")
        missing_keys = [item for item in required if item not in normalized["properties"]]
        if missing_keys:
            raise CustomPythonToolContractError(
                f"required at {path} references unknown properties: {', '.join(sorted(missing_keys))}."
            )
        normalized["required"] = required
        additional = normalized.get("additionalProperties", False)
        if not isinstance(additional, bool):
            raise CustomPythonToolContractError(f"additionalProperties at {path} must be a boolean.")
        normalized["additionalProperties"] = additional
    elif schema_type == "array":
        items = normalized.get("items")
        if items is None:
            raise CustomPythonToolContractError(f"Array schema at {path} must define items.")
        normalized["items"] = _validate_property_schema(items, path=_json_path(path, "items"))

    for int_key in ("minItems", "maxItems", "minLength", "maxLength"):
        value = normalized.get(int_key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise CustomPythonToolContractError(f"{int_key} at {path} must be a non-negative integer.")

    for num_key in ("minimum", "maximum"):
        value = normalized.get(num_key)
        if value is not None and not isinstance(value, (int, float)):
            raise CustomPythonToolContractError(f"{num_key} at {path} must be a number.")

    return normalized


def normalize_tool_definition(raw_definition: Any) -> dict[str, Any]:
    """Normalize a tool definition exported by custom source into the runtime contract."""

    if not isinstance(raw_definition, dict):
        raise CustomPythonToolContractError("TOOL_DEFINITION must be a dictionary.")

    name = str(raw_definition.get("name") or "").strip()
    if not TOOL_NAME_RE.match(name):
        raise CustomPythonToolContractError(
            "Tool name must start with a letter and contain only letters, numbers, and underscores (max 64 chars)."
        )
    if name.startswith("mcp_"):
        raise CustomPythonToolContractError("Tool names starting with 'mcp_' are reserved.")

    description = str(raw_definition.get("description") or "").strip()
    if not description:
        raise CustomPythonToolContractError("Tool description is required.")

    display_name = str(raw_definition.get("display_name") or raw_definition.get("title") or name).strip()
    if not display_name:
        display_name = name

    parameters = raw_definition.get("parameters")
    if parameters is None:
        parameters = raw_definition.get("input_schema")
    normalized_parameters = _validate_property_schema(parameters, path="parameters")
    if normalized_parameters.get("type") != "object":
        raise CustomPythonToolContractError("Tool parameters must be a JSON-schema object with type='object'.")

    return {
        "name": name,
        "display_name": display_name,
        "type": "function",
        "description": description,
        "parameters": normalized_parameters,
    }


def validate_tool_arguments_against_schema(arguments: Any, schema: dict[str, Any], *, path: str = "arguments") -> None:
    """Validate runtime tool arguments against the normalized custom tool schema."""

    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(arguments, dict):
            raise _argument_validation_error(f"{path} must be an object.", path=path)
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in arguments:
                missing_path = _json_path(path, key)
                raise _argument_validation_error(
                    f"{missing_path} is required.",
                    code="custom_tool_argument_required",
                    path=missing_path,
                )
        if schema.get("additionalProperties") is False:
            unknown = [key for key in arguments.keys() if key not in properties]
            if unknown:
                sorted_unknown = sorted(unknown)
                raise _argument_validation_error(
                    f"{path} contains unsupported keys: {', '.join(sorted_unknown)}.",
                    path=_json_path(path, sorted_unknown[0]),
                )
        for key, value in arguments.items():
            child_schema = properties.get(key)
            if child_schema is None:
                continue
            validate_tool_arguments_against_schema(value, child_schema, path=_json_path(path, key))
        return

    if schema_type == "array":
        if not isinstance(arguments, list):
            raise _argument_validation_error(f"{path} must be an array.", path=path)
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(arguments) < min_items:
            raise _argument_validation_error(
                f"{path} must contain at least {min_items} items.",
                path=path,
            )
        if isinstance(max_items, int) and len(arguments) > max_items:
            raise _argument_validation_error(
                f"{path} must contain at most {max_items} items.",
                path=path,
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(arguments):
                validate_tool_arguments_against_schema(item, item_schema, path=f"{path}[{index}]")
        return

    if schema_type == "string":
        if not isinstance(arguments, str):
            raise _argument_validation_error(f"{path} must be a string.", path=path)
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(arguments) < min_length:
            raise _argument_validation_error(
                f"{path} must be at least {min_length} characters.",
                path=path,
            )
        if isinstance(max_length, int) and len(arguments) > max_length:
            raise _argument_validation_error(
                f"{path} must be at most {max_length} characters.",
                path=path,
            )
    elif schema_type == "integer":
        if not isinstance(arguments, int) or isinstance(arguments, bool):
            raise _argument_validation_error(f"{path} must be an integer.", path=path)
    elif schema_type == "number":
        if not isinstance(arguments, (int, float)) or isinstance(arguments, bool):
            raise _argument_validation_error(f"{path} must be a number.", path=path)
    elif schema_type == "boolean":
        if not isinstance(arguments, bool):
            raise _argument_validation_error(f"{path} must be a boolean.", path=path)

    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and isinstance(arguments, (int, float)) and arguments < minimum:
        raise _argument_validation_error(f"{path} must be >= {minimum}.", path=path)
    if maximum is not None and isinstance(arguments, (int, float)) and arguments > maximum:
        raise _argument_validation_error(f"{path} must be <= {maximum}.", path=path)

    enum_values = schema.get("enum")
    if enum_values is not None and arguments not in enum_values:
        raise _argument_validation_error(
            f"{path} must be one of: {', '.join(map(str, enum_values))}.",
            path=path,
        )


def serialize_content(value: Any) -> str:
    """Serialize arbitrary tool output into the string content field expected by the UI."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize_widget_payload(value: Any) -> dict[str, Any] | None:
    """Validate and enrich a custom-tool widget returned by backend Python code."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise CustomPythonToolContractError("widget must be an object when returned from run_tool.")

    widget = dict(value)
    html_value = widget.get("html")
    if html_value is None:
        raise CustomPythonToolContractError("widget.html is required when widget is returned from run_tool.")
    widget["html"] = serialize_content(html_value)
    if not widget["html"].strip():
        raise CustomPythonToolContractError("widget.html must not be empty when widget is returned from run_tool.")

    widget_type = str(widget.get("type") or "custom_python").strip() or "custom_python"
    widget["type"] = widget_type

    explicit_allow_scripts = widget.get("allow_scripts")
    if explicit_allow_scripts is None:
        # Custom Python tools are administrator-managed backend code. If the
        # returned widget contains a script tag, make the script-backed render
        # mode explicit so the chat renderer can mount it in an opaque iframe.
        allow_scripts = "<script" in widget["html"].lower()
    elif isinstance(explicit_allow_scripts, bool):
        allow_scripts = explicit_allow_scripts
    else:
        raise CustomPythonToolContractError("widget.allow_scripts must be a boolean when provided.")
    widget["allow_scripts"] = allow_scripts

    render_mode = str(widget.get("render_mode") or "").strip().lower()
    if not render_mode:
        render_mode = "iframe" if allow_scripts else "inline"
    if render_mode not in WIDGET_RENDER_MODES:
        supported = ", ".join(sorted(WIDGET_RENDER_MODES))
        raise CustomPythonToolContractError(f"widget.render_mode must be one of: {supported}.")
    if allow_scripts and render_mode != "iframe":
        raise CustomPythonToolContractError("widget.allow_scripts=true requires widget.render_mode='iframe'.")
    widget["render_mode"] = render_mode

    model_context = widget.get("model_context")
    if model_context is not None:
        try:
            json.dumps(model_context, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise CustomPythonToolContractError("widget.model_context must be JSON serializable.") from exc

    return widget


def normalize_tool_output(raw_output: Any, *, stdout: str = "", stderr: str = "") -> dict[str, Any]:
    """Normalize custom tool return values into the multi-modal output contract used by the app."""

    normalized: dict[str, Any]
    if isinstance(raw_output, dict) and any(key in raw_output for key in OUTPUT_KEYS):
        normalized = dict(raw_output)
    else:
        normalized = {
            "result": raw_output,
        }

    if "result" not in normalized:
        if "content" in normalized:
            normalized["result"] = normalized.get("content")
        else:
            list_like_output_only = (
                isinstance(raw_output, dict)
                and bool(raw_output)
                and all(
                    key in OUTPUT_KEYS and isinstance(value, (list, tuple))
                    for key, value in raw_output.items()
                )
            )
            normalized["result"] = None if list_like_output_only else raw_output

    content = normalized.get("content")
    if content is None:
        normalized["content"] = serialize_content(normalized.get("result"))
    else:
        normalized["content"] = serialize_content(content)

    for list_key in ("documents", "images", "videos", "audios", "youtube", "webpages", "stream_events"):
        value = normalized.get(list_key)
        if value is None:
            normalized[list_key] = []
        elif isinstance(value, list):
            normalized[list_key] = value
        else:
            raise CustomPythonToolContractError(f"{list_key} must be a list when returned from run_tool.")

    meta = normalized.get("tool_meta")
    if meta is None:
        alt_meta = normalized.get("meta")
        if alt_meta is None:
            meta = {}
        elif not isinstance(alt_meta, dict):
            raise CustomPythonToolContractError("meta must be an object.")
        else:
            meta = dict(alt_meta)
    elif not isinstance(meta, dict):
        raise CustomPythonToolContractError("tool_meta must be an object.")
    else:
        meta = dict(meta)

    if stdout:
        meta["python_stdout"] = stdout
    if stderr:
        meta["python_stderr"] = stderr
    normalized["tool_meta"] = meta
    normalized["widget"] = _normalize_widget_payload(normalized.get("widget"))

    return normalized
