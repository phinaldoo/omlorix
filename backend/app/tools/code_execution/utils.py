import base64
import uuid
import logging
import mimetypes
import httpx
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.database import SessionLocal
from app.chats.models import Chats
from app.files.models import get_file
from app.files.utils import get_file_category, materialize_file_record, persist_generated_file_bytes
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.settings.models import get_settings_page_data
from app.tools.errors import SafeToolExecutionError
from app.service_connections.utils import (
    SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES,
    SERVICE_PURPOSE_CODE_EXECUTION,
    get_service_connection_candidates,
    has_healthy_service_connection_capability,
    parse_capabilities_payload,
    record_service_connection_runtime_status,
)
logger = logging.getLogger(__name__)
_CONTAINER_META_KEY = "code_execution"
_VALID_LANGUAGES = {"python", "bash"}
_VALID_TOOL_TYPES = {"public", "internal"}
DEFAULT_CODE_EXECUTION_TYPE = "public"
_EXECUTION_TIMEOUT_METADATA_KEY = "execution_timeout_seconds"
_CODE_EXECUTION_HEALTH_TIMEOUT_SECONDS = 10
_CODE_EXECUTION_TRANSPORT_GRACE_SECONDS = 10
_LEGACY_CODE_EXECUTION_TRANSPORT_TIMEOUT_SECONDS = 130


class _ContainerUnavailableError(RuntimeError):
    pass


class _CodeExecutionServiceUnavailableError(RuntimeError):
    pass


class _CodeExecutionSessionCapacityError(RuntimeError):
    """Internal marker for a trusted gateway response that reports no session slot."""


class CodeExecutionSessionCapacityError(SafeToolExecutionError):
    """Safe model-facing error for temporary code-execution session exhaustion."""

    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__(
            code="code_execution_session_capacity_unavailable",
            safe_message=(
                "Code execution is temporarily unavailable because no container "
                "session slot is available for this request. Do not retry code "
                "execution again in this response. Inform the user and suggest "
                "trying again shortly; an existing session may take up to 20 "
                "minutes to expire."
            ),
            detail=detail,
            allow_same_response_retry=False,
        )


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_code_execution_runtime_config(db=None) -> dict:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        config = {"max_output_length": 50000}
        settings_data = get_settings_page_data(db, "code_execution")
        config["max_output_length"] = settings_data.get("max_output_length", 50000) or 50000
        return config
    finally:
        if close_db:
            db.close()


def code_execution_supports_external_pip_packages(db) -> bool:
    """Return whether the current healthy connection pool can install packages."""
    if db is None:
        return False
    try:
        return has_healthy_service_connection_capability(
            db,
            SERVICE_PURPOSE_CODE_EXECUTION,
            SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES,
        )
    except Exception:
        logger.exception("Failed to resolve code execution pip-install capability")
        return False


def _get_code_execution_runtime_config(db=None) -> dict:
    """Backward-compatible shim for older tests and internal callers."""
    return get_code_execution_runtime_config(db)


def _truncate_execution_text(value: Any, max_length: int) -> str:
    text = str(value or "")
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"\n... (output truncated at {max_length} characters)"


def normalize_code_execution_tool_type(
    tool_type: Any,
    *,
    tool_name: str = "code_execution",
    default_type: str | None = None,
) -> str:
    if default_type is None:
        default_type = "internal" if str(tool_name).strip() == "code_execution_internal" else DEFAULT_CODE_EXECUTION_TYPE

    resolved_type = str(tool_type or default_type).strip().lower()
    if resolved_type not in _VALID_TOOL_TYPES:
        raise ValueError(
            f"{tool_name} argument 'type' must be one of: {', '.join(sorted(_VALID_TOOL_TYPES))}"
        )
    return resolved_type


def build_code_execution_tool_schema(
    *,
    name: str = "code_execution",
    description: str | None = None,
    default_type: str = DEFAULT_CODE_EXECUTION_TYPE,
    allow_pip_packages: bool = True,
) -> Dict[str, Any]:
    """Build the model-facing code execution schema for the current policy.

    Network access is controlled exclusively by the execution service environment.
    Package installation is controlled by capabilities reported by the healthy
    service-connection pool. Unsupported arguments are omitted so the model
    receives a schema that matches the execution environment it can actually use.
    """
    resolved_default_type = normalize_code_execution_tool_type(
        default_type,
        tool_name=name,
        default_type=default_type,
    )
    schema_description = description or (
        "Execute code in the user's configured persistent execution environment (python or bash). "
        "Set type to 'public' to expose results and generated files to the user, or 'internal' "
        "for model-only reasoning while reusing the configured environment."
    )
    # Keep the persistent-file workflow in the schema as well as the broader
    # system instructions. Tool descriptions remain close to the model's call
    # site and are therefore less likely to be overlooked during a correction.
    schema_description += (
        " For non-trivial or iterative work, first create a source file with a stable name in the "
        "container working directory and then execute that file. Reuse the same file in later calls; "
        "when correcting it, make the smallest targeted edit and rerun it instead of replacing the "
        "complete file. Use inline code only for short, disposable commands or calculations."
    )
    schema_description += (
        " If the service reports that code execution is unavailable, do not retry the same call; "
        "inform the user that the service is currently unavailable."
    )
    if not allow_pip_packages:
        schema_description += (
            " Additional packages cannot be installed in the currently configured execution "
            "environment; use only its preinstalled packages."
        )
    properties: Dict[str, Any] = {
        "type": {
            "type": "string",
            "enum": ["public", "internal"],
            "default": resolved_default_type,
            "description": (
                "Execution visibility. Use 'public' to expose output and generated files to the user. "
                "Use 'internal' for model-only reasoning. Defaults to "
                f"'{resolved_default_type}'."
            ),
        },
        "language": {
            "type": "string",
            "enum": ["python", "bash"],
            "description": (
                "Execution language. Use 'python' for scripts/notebooks and 'bash' for shell commands. "
                "Defaults to 'python'."
            ),
        },
        "code": {
            "type": "string",
            "description": (
                "Code to execute for the selected language. For non-trivial or iterative work, use this "
                "to create or make a targeted edit to a persistent source file and execute that file."
            ),
        },
        "file_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional Omlorix file IDs from user files to mount into the container working directory before execution. "
                "Each entry must be the exact file_id value only, not the visible file name, not the file name without "
                "its extension, and not the file name with its extension."
            ),
        },
    }
    if allow_pip_packages:
        properties["pip_packages"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional list of additional PyPI packages to install before Python execution "
                '(for example ["cowsay", "rich"]). Ignored for bash.'
            ),
        }
    return {
        "name": name,
        "type": "function",
        "description": schema_description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": ["code"],
        },
    }


def _save_base64_output_file(
    base64_data: str,
    mime_type: str,
    user_id: str,
    original_name: str,
    db,
) -> Dict[str, Any]:
    file_bytes = base64.b64decode(base64_data)

    safe_name = (original_name or "output").strip() or "output"
    original_suffix = Path(safe_name).suffix
    guessed_suffix = mimetypes.guess_extension(mime_type or "") or ""
    ext = original_suffix or guessed_suffix or ".bin"
    if not ext.startswith("."):
        ext = f".{ext}"

    file_type = (mime_type or "").strip()
    if not file_type:
        guessed_type, _ = mimetypes.guess_type(safe_name)
        file_type = guessed_type or "application/octet-stream"
    if file_type == "application/octet-stream" and ext:
        guessed_from_ext, _ = mimetypes.guess_type(f"output{ext}")
        if guessed_from_ext:
            file_type = guessed_from_ext

    file_category = get_file_category(file_type)
    stored_file_id = str(uuid.uuid4())
    stored_file_name = f"{stored_file_id}{ext}"

    if not safe_name.lower().endswith(ext.lower()):
        safe_name = f"{safe_name}{ext}"

    meta = {
        "original_filename": safe_name,
        "origin": "assistant",
        "code_execution": True,
    }

    file_record = persist_generated_file_bytes(
        db,
        user_id=str(user_id),
        original_filename=safe_name,
        file_bytes=file_bytes,
        file_type=file_type,
        file_category=file_category,
        file_id=stored_file_id,
        file_name=stored_file_name,
        meta=meta,
    )

    return {
        "file_id": file_record.id,
        "name": safe_name,
        "mime_type": file_type,
        "file_category": file_category,
        "size": len(file_bytes),
    }


def _get_chat_record(db, user_id: str, chat_id: str | None):
    if not chat_id or str(chat_id).strip() in {"", "temp"}:
        return None
    return (
        db.query(Chats)
        .filter(Chats.id == str(chat_id), Chats.user_id == str(user_id))
        .first()
    )


def _get_chat_container_binding(db, user_id: str, chat_id: str | None) -> dict[str, Any]:
    chat = _get_chat_record(db, user_id, chat_id)
    if not chat:
        return {}
    meta = chat.meta if isinstance(chat.meta, dict) else {}
    code_meta = meta.get(_CONTAINER_META_KEY) if isinstance(meta.get(_CONTAINER_META_KEY), dict) else {}
    if str(code_meta.get("user_id") or "").strip() != str(user_id):
        return {}
    if str(code_meta.get("chat_id") or "").strip() != str(chat_id or "").strip():
        return {}
    return code_meta


def _get_chat_container_id(db, user_id: str, chat_id: str | None, base_url: str) -> Optional[str]:
    code_meta = _get_chat_container_binding(db, user_id, chat_id)
    if str(code_meta.get("base_url") or "").strip().rstrip("/") != str(base_url or "").strip().rstrip("/"):
        return None
    container_id = code_meta.get("container_id")
    if isinstance(container_id, str) and container_id.strip():
        return container_id.strip()
    return None


def _get_chat_bound_base_url(db, user_id: str, chat_id: str | None) -> str:
    code_meta = _get_chat_container_binding(db, user_id, chat_id)
    return str(code_meta.get("base_url") or "").strip().rstrip("/")


def _prefer_bound_service_connection(
    connections: list[dict[str, Any]],
    bound_base_url: str,
) -> list[dict[str, Any]]:
    normalized_bound_base_url = str(bound_base_url or "").strip().rstrip("/")
    if not normalized_bound_base_url:
        return connections
    return sorted(
        connections,
        key=lambda connection: (
            str(connection.get("base_url") or "").strip().rstrip("/") != normalized_bound_base_url
        ),
    )


def _set_chat_container_id(db, user_id: str, chat_id: str | None, container_id: str, base_url: str) -> None:
    chat = _get_chat_record(db, user_id, chat_id)
    if not chat:
        return
    meta = dict(chat.meta) if isinstance(chat.meta, dict) else {}
    code_meta = dict(meta.get(_CONTAINER_META_KEY)) if isinstance(meta.get(_CONTAINER_META_KEY), dict) else {}
    code_meta.update(
        {
            "container_id": container_id,
            "base_url": base_url.rstrip("/"),
            "user_id": str(user_id),
            "chat_id": str(chat_id or ""),
            "updated_at": _utc_iso(),
        }
    )
    meta[_CONTAINER_META_KEY] = code_meta
    chat.meta = meta
    db.commit()


def _clear_chat_container_id(db, user_id: str, chat_id: str | None) -> None:
    chat = _get_chat_record(db, user_id, chat_id)
    if not chat:
        return

    meta = dict(chat.meta) if isinstance(chat.meta, dict) else {}
    code_meta = dict(meta.get(_CONTAINER_META_KEY)) if isinstance(meta.get(_CONTAINER_META_KEY), dict) else {}
    if not code_meta:
        return

    code_meta.pop("container_id", None)
    code_meta["updated_at"] = _utc_iso()

    meta[_CONTAINER_META_KEY] = code_meta

    chat.meta = meta
    db.commit()


def _container_is_active(
    client: httpx.Client,
    base_url: str,
    headers: Dict[str, str],
    container_id: str,
) -> bool:
    status_url = f"{base_url.rstrip('/')}/containers/{container_id}"
    response = client.get(status_url, headers=headers)
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else "Unknown error"
        raise RuntimeError(
            f"Code execution service returned status {response.status_code} while checking container: {detail}"
        )
    try:
        payload = response.json()
    except Exception:
        return True
    status_value = payload.get("status")
    if isinstance(status_value, str) and status_value.strip():
        return status_value.strip().lower() in {"active", "running"}
    return True


def _create_container(
    client: httpx.Client,
    base_url: str,
    headers: Dict[str, str],
) -> str:
    create_url = f"{base_url.rstrip('/')}/containers"
    response = client.post(
        create_url,
        json={},
        headers=headers,
    )
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else "Unknown error"
        # Only the gateway's two explicit active-session limit messages are
        # safe to classify as temporary capacity exhaustion. Other 429s may be
        # request-rate limits or arbitrary upstream responses and retain the
        # normal generic error treatment.
        normalized_detail = detail.lower()
        if (
            response.status_code == 429
            and "maximum number of active container sessions" in normalized_detail
        ):
            raise _CodeExecutionSessionCapacityError(
                f"Code execution service returned status 429 while creating container: {detail}"
            )
        raise RuntimeError(
            f"Code execution service returned status {response.status_code} while creating container: {detail}"
        )
    payload = response.json()
    container_id = payload.get("container_id")
    if not container_id:
        raise RuntimeError("Code execution service did not return a container_id.")
    return str(container_id)


def _ensure_container(
    client: httpx.Client,
    db,
    user_id: str,
    chat_id: str | None,
    base_url: str,
    headers: Dict[str, str],
) -> str:
    existing_container_id = _get_chat_container_id(db, user_id, chat_id, base_url)
    if existing_container_id:
        try:
            if _container_is_active(client, base_url, headers, existing_container_id):
                return existing_container_id
        except Exception as exc:
            logger.warning("Failed to verify cached container %s: %s", existing_container_id, exc)
            _clear_chat_container_id(db, user_id, chat_id)

    container_id = _create_container(
        client=client,
        base_url=base_url,
        headers=headers,
    )
    _set_chat_container_id(
        db=db,
        user_id=user_id,
        chat_id=chat_id,
        container_id=container_id,
        base_url=base_url,
    )
    return container_id


def _prepare_input_files_payload(
    db,
    user_id: str,
    file_ids: Optional[List[str]],
) -> List[Dict[str, str]]:
    if not file_ids:
        return []

    unique_file_ids: list[str] = []
    seen: set[str] = set()
    for raw_file_id in file_ids:
        if not isinstance(raw_file_id, str):
            continue
        file_id = raw_file_id.strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        unique_file_ids.append(file_id)

    payload: list[dict[str, str]] = []
    for file_id in unique_file_ids:
        file_record = get_file(db, file_id, str(user_id))
        if not file_record:
            logger.warning("Skipping missing or unauthorized input file id for code execution: %s", file_id)
            continue
        try:
            file_path = materialize_file_record(file_record, str(user_id))
            file_bytes = file_path.read_bytes()
            meta = file_record.meta if isinstance(file_record.meta, dict) else {}
            original_name = meta.get("original_filename") if isinstance(meta.get("original_filename"), str) else None
            payload.append(
                {
                    "name": original_name or file_record.file_name,
                    "content": base64.b64encode(file_bytes).decode("ascii"),
                }
            )
        except Exception as exc:
            logger.warning("Failed to include input file %s for code execution: %s", file_id, exc)

    return payload


def _execute_request(
    client: httpx.Client,
    base_url: str,
    headers: Dict[str, str],
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    execute_url = f"{base_url.rstrip('/')}/execute"
    response = client.post(
        execute_url,
        json=request_payload,
        headers=headers,
    )

    if response.status_code == 404:
        raise _ContainerUnavailableError("Container not found")
    if response.status_code >= 400:
        detail = response.text[:500] if response.text else "Unknown error"
        lowered_detail = detail.lower()
        if "container" in lowered_detail and ("not found" in lowered_detail or "missing" in lowered_detail):
            raise _ContainerUnavailableError(detail)
        raise RuntimeError(f"Code execution service returned status {response.status_code}: {detail}")

    return response.json()


def _check_service_health(
    client: httpx.Client,
    base_url: str,
    headers: Dict[str, str],
) -> dict[str, Any]:
    # `/health` is the canonical authenticated gateway readiness endpoint.
    # The historical `/healthz` alias was removed from the gateway contract.
    health_url = f"{base_url.rstrip('/')}/health"
    try:
        response = client.get(health_url, headers=headers)
    except httpx.TimeoutException as exc:
        raise _CodeExecutionServiceUnavailableError(
            "Code execution service is not available right now (health check timed out). "
            "Retrying will not help until the service is reachable again."
        ) from exc
    except httpx.RequestError as exc:
        raise _CodeExecutionServiceUnavailableError(
            "Code execution service is not available right now (health check failed to connect). "
            "Retrying will not help until the service is reachable again."
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        detail = response.text[:500] if response.text else "Health check failed"
        raise _CodeExecutionServiceUnavailableError(
            "Code execution service is not available right now "
            f"(health check returned {response.status_code}: {detail}). "
            "Retrying will not help until the service is reachable again."
        )

    try:
        payload = response.json()
    except ValueError:
        return {}
    capabilities: dict[str, Any] = parse_capabilities_payload(payload)
    raw_execution_timeout = payload.get(_EXECUTION_TIMEOUT_METADATA_KEY)
    if (
        isinstance(raw_execution_timeout, int)
        and not isinstance(raw_execution_timeout, bool)
        and raw_execution_timeout > 0
    ):
        # The service advertises this value only so the HTTP client can wait
        # longer than the sandbox watchdog. It is never sent back as a request
        # option and therefore cannot control execution duration.
        capabilities[_EXECUTION_TIMEOUT_METADATA_KEY] = raw_execution_timeout
    return capabilities


def _code_execution_transport_timeout_seconds(service_health: dict[str, Any]) -> int:
    """Return an HTTP deadline that stays outside the sandbox execution policy."""
    raw_execution_timeout = service_health.get(_EXECUTION_TIMEOUT_METADATA_KEY)
    if (
        isinstance(raw_execution_timeout, int)
        and not isinstance(raw_execution_timeout, bool)
        and raw_execution_timeout > 0
    ):
        return raw_execution_timeout + _CODE_EXECUTION_TRANSPORT_GRACE_SECONDS

    # Older gateways do not advertise their sandbox watchdog. This fallback is
    # transport-only and preserves compatibility while services are upgraded.
    return _LEGACY_CODE_EXECUTION_TRANSPORT_TIMEOUT_SECONDS


def _is_retryable_code_service_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    if "code execution service returned status" not in message:
        return False
    return any(token in message for token in (" 401", " 403", " 429", " 500", " 502", " 503", " 504"))


def _connection_headers(connection: dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    api_key = str(connection.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def execute_code(
    code: str,
    user_id: str,
    chat_id: Optional[str] = None,
    language: Optional[str] = "python",
    pip_packages: Optional[List[str]] = None,
    file_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required for code execution")
    if not code or not code.strip():
        raise ValueError("code is required for code execution")

    exec_language = str(language or "python").strip().lower()
    if exec_language not in _VALID_LANGUAGES:
        raise ValueError(f"Invalid language '{exec_language}'. Supported values: python, bash")

    runtime_config = _get_code_execution_runtime_config()
    max_output_length = runtime_config.get("max_output_length", 50000)

    clean_pip_packages: list[str] = []
    if exec_language == "python" and pip_packages:
        clean_pip_packages = [
            package.strip()
            for package in pip_packages
            if isinstance(package, str) and package.strip()
        ]

    db = SessionLocal()
    try:
        input_files_payload = _prepare_input_files_payload(
            db=db,
            user_id=str(user_id),
            file_ids=file_ids,
        )

        connections = get_service_connection_candidates(db, SERVICE_PURPOSE_CODE_EXECUTION)

        if not connections:
            raise ValueError("Code execution service is not configured. Add a service connection in admin settings.")

        previous_base_url = _get_chat_bound_base_url(db, str(user_id), chat_id)
        connections = _prefer_bound_service_connection(connections, previous_base_url)

        result: Dict[str, Any] | None = None
        selected_connection: dict[str, Any] | None = None
        last_service_error: Exception | None = None
        session_capacity_error: _CodeExecutionSessionCapacityError | None = None
        healthy_pip_unsupported_service_seen = False
        healthy_pip_capable_service_seen = False

        for connection in connections:
            if not connection:
                continue
            base_url = str(connection.get("base_url") or "").strip().rstrip("/")
            if not base_url:
                continue
            headers = _connection_headers(connection)
            try:
                try:
                    assert_url_allowed(db, url=base_url, feature="Code execution service")
                except OutboundRequestBlockedError as exc:
                    raise RuntimeError(str(exc)) from exc

                with httpx.Client(timeout=_CODE_EXECUTION_HEALTH_TIMEOUT_SECONDS) as health_client:
                    service_health = _check_service_health(
                        client=health_client,
                        base_url=base_url,
                        headers=headers,
                    )
                if not isinstance(service_health, dict):
                    service_health = {}
                capabilities = {
                    name: value
                    for name, value in service_health.items()
                    if isinstance(value, bool)
                }
                transport_timeout = _code_execution_transport_timeout_seconds(service_health)
                record_service_connection_runtime_status(
                    db,
                    connection,
                    SERVICE_PURPOSE_CODE_EXECUTION,
                    available=True,
                    message="Available",
                    capabilities=capabilities,
                )

                if clean_pip_packages:
                    if capabilities.get(SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES) is not True:
                        healthy_pip_unsupported_service_seen = True
                        continue
                    healthy_pip_capable_service_seen = True

                with httpx.Client(timeout=transport_timeout) as client:
                    container_id = _ensure_container(
                        client=client,
                        db=db,
                        user_id=str(user_id),
                        chat_id=chat_id,
                        base_url=base_url,
                        headers=headers,
                    )

                    request_payload: Dict[str, Any] = {
                        "container_id": container_id,
                        "language": exec_language,
                        "code": code,
                    }

                    if clean_pip_packages:
                        request_payload["pip_packages"] = clean_pip_packages

                    if input_files_payload:
                        request_payload["files"] = input_files_payload

                    try:
                        result = _execute_request(
                            client=client,
                            base_url=base_url,
                            headers=headers,
                            request_payload=request_payload,
                        )
                    except _ContainerUnavailableError:
                        container_id = _create_container(
                            client=client,
                            base_url=base_url,
                            headers=headers,
                        )
                        _set_chat_container_id(
                            db=db,
                            user_id=str(user_id),
                            chat_id=chat_id,
                            container_id=container_id,
                            base_url=base_url,
                        )
                        request_payload["container_id"] = container_id
                        result = _execute_request(
                            client=client,
                            base_url=base_url,
                            headers=headers,
                            request_payload=request_payload,
                        )
                selected_connection = connection
                break
            except _CodeExecutionServiceUnavailableError as exc:
                last_service_error = exc
                record_service_connection_runtime_status(
                    db,
                    connection,
                    SERVICE_PURPOSE_CODE_EXECUTION,
                    available=False,
                    message=str(exc),
                    failure_scope="service",
                )
                continue
            except httpx.TimeoutException:
                last_service_error = RuntimeError(
                    "Code execution service request exceeded its transport deadline"
                )
                continue
            except httpx.RequestError as exc:
                last_service_error = RuntimeError(f"Failed to connect to code execution service: {exc}")
                continue
            except _CodeExecutionSessionCapacityError as exc:
                # Capacity is request-scoped, so do not mark the shared service
                # unhealthy. Keep trying any other configured healthy service
                # before returning the actionable safe error to the model.
                last_service_error = exc
                session_capacity_error = exc
                continue
            except RuntimeError as exc:
                if _is_retryable_code_service_error(exc) or "code execution service blocked" in str(exc).lower():
                    last_service_error = exc
                    continue
                raise

        if result is None:
            if session_capacity_error is not None:
                raise CodeExecutionSessionCapacityError(
                    detail=str(session_capacity_error),
                ) from session_capacity_error
            if (
                clean_pip_packages
                and healthy_pip_unsupported_service_seen
                and not healthy_pip_capable_service_seen
            ):
                raise RuntimeError(
                    "No available configured code execution service supports external pip package installation."
                )
            if last_service_error:
                raise RuntimeError(str(last_service_error))
            raise RuntimeError("No available code execution service connection could handle the request.")

        service_connection_meta = {
            "id": selected_connection.get("id") if selected_connection else "",
            "name": selected_connection.get("name") if selected_connection else "",
            "base_url": selected_connection.get("base_url") if selected_connection else "",
            "legacy": bool(selected_connection.get("legacy")) if selected_connection else False,
        }

        stdout = result.get("stdout", "") or ""
        stderr = result.get("stderr", "") or ""
        error = result.get("error")
        error_type = result.get("error_type")
        execution_time = result.get("execution_time", 0)
        timed_out = result.get("timed_out", False)
        files = result.get("files", []) or []
        execution_id = result.get("execution_id", "")
        environment_reset = bool(
            previous_base_url
            and selected_connection
            and str(selected_connection.get("base_url") or "").strip().rstrip("/") != previous_base_url
        )
        
        stdout = _truncate_execution_text(stdout, max_output_length)
        stderr = _truncate_execution_text(stderr, max_output_length)
        error = _truncate_execution_text(error, max_output_length) if error not in (None, "", False) else error

        saved_files: List[Dict[str, Any]] = []
        for idx, file_info in enumerate(files):
            file_name = file_info.get("name", f"output_{idx}")
            file_content = file_info.get("content", "")
            file_mime_type = file_info.get("mime_type", "application/octet-stream")
            if file_content:
                try:
                    saved_info = _save_base64_output_file(
                        base64_data=file_content,
                        mime_type=file_mime_type,
                        user_id=user_id,
                        original_name=file_name,
                        db=db,
                    )
                    saved_files.append(saved_info)
                except Exception as save_exc:
                    logger.warning(f"Failed to save code execution output file {file_name}: {save_exc}")

        execution_error = error not in (None, "", False) or bool(timed_out)
        tool_result = {
            "execution_id": execution_id,
            "language": exec_language,
            "service_connection": service_connection_meta,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
            "error_type": error_type,
            "execution_succeeded": not execution_error,
            "execution_error": execution_error,
            "tool_transport_succeeded": True,
            "execution_time": execution_time,
            "timed_out": timed_out,
            "files_generated": len(saved_files),
            "input_files_loaded": len(input_files_payload),
            "environment_reset": environment_reset,
            "output_files": [
                {
                    "file_id": file_entry.get("file_id"),
                    "name": file_entry.get("name"),
                    "mime_type": file_entry.get("mime_type"),
                    "file_category": file_entry.get("file_category"),
                    "size": file_entry.get("size"),
                }
                for file_entry in saved_files
            ],
        }

        return {
            "result": tool_result,
            "saved_files": saved_files,
        }

    except _CodeExecutionServiceUnavailableError:
        raise
    except httpx.TimeoutException:
        raise RuntimeError("Code execution service request exceeded its transport deadline")
    except httpx.RequestError as req_err:
        raise RuntimeError(f"Failed to connect to code execution service: {req_err}")
    finally:
        db.close()


def normalize_code_execution_tool_args(
    tool_args: Dict[str, Any] | None,
    *,
    tool_name: str = "code_execution",
    default_type: str | None = None,
) -> Dict[str, Any]:
    normalized_args = tool_args if isinstance(tool_args, dict) else {}
    code = normalized_args.get("code")
    if not code or not str(code).strip():
        raise ValueError(f"{tool_name} requires a non-empty 'code' argument.")

    pip_packages = normalized_args.get("pip_packages")
    file_ids = normalized_args.get("file_ids")
    if pip_packages and not isinstance(pip_packages, list):
        pip_packages = None
    if file_ids and not isinstance(file_ids, list):
        file_ids = None

    return {
        "type": normalize_code_execution_tool_type(
            normalized_args.get("type"),
            tool_name=tool_name,
            default_type=default_type,
        ),
        "code": str(code),
        "language": normalized_args.get("language"),
        "pip_packages": pip_packages,
        "file_ids": file_ids,
    }


def format_code_execution_tool_output(
    exec_result: Dict[str, Any] | None,
    *,
    include_file_ids: bool = False,
) -> str:
    payload = exec_result if isinstance(exec_result, dict) else {}
    tool_result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
    saved_files = payload.get("saved_files", []) if isinstance(payload.get("saved_files"), list) else []

    saved_file_summaries: list[str] = []
    for saved_file in saved_files:
        if not isinstance(saved_file, dict):
            continue
        file_name = saved_file.get("name", "output.bin")
        mime_type = saved_file.get("mime_type", "application/octet-stream")
        file_id = saved_file.get("file_id")
        if include_file_ids and file_id:
            saved_file_summaries.append(f"{file_name} ({mime_type}) [file_id={file_id}]")
        else:
            saved_file_summaries.append(f"{file_name} ({mime_type})")

    content_parts: list[str] = []
    if tool_result.get("language"):
        content_parts.append(f"language: {tool_result.get('language')}")
    if tool_result.get("stdout"):
        content_parts.append(f"stdout:\n{tool_result['stdout']}")
    if tool_result.get("stderr"):
        content_parts.append(f"stderr:\n{tool_result['stderr']}")
    if tool_result.get("error"):
        content_parts.append(f"error ({tool_result.get('error_type', 'unknown')}): {tool_result['error']}")
    if tool_result.get("timed_out"):
        content_parts.append("Execution timed out.")
    if tool_result.get("input_files_loaded", 0) > 0:
        content_parts.append(f"Loaded {tool_result['input_files_loaded']} input file(s) into the container.")
    if tool_result.get("environment_reset"):
        content_parts.append(
            "The previous execution service was unavailable or incompatible with this request, "
            "so execution continued in a new environment. State from the previous container is not available here."
        )
    if tool_result.get("files_generated", 0) > 0:
        content_parts.append(f"Generated {tool_result['files_generated']} file(s).")
        if saved_file_summaries:
            content_parts.append("Output files:\n- " + "\n- ".join(saved_file_summaries))

    return "\n\n".join(content_parts) if content_parts else "Code executed successfully with no output."


def execute_code_tool_call(
    tool_args: Dict[str, Any] | None,
    *,
    user_id: str,
    chat_id: Optional[str] = None,
    tool_name: str = "code_execution",
    include_file_ids: bool = False,
    default_type: str | None = None,
) -> Dict[str, Any]:
    request = normalize_code_execution_tool_args(
        tool_args,
        tool_name=tool_name,
        default_type=default_type,
    )
    resolved_tool_type = request["type"]

    try:
        exec_result = execute_code(
            code=request["code"],
            user_id=user_id,
            chat_id=chat_id,
            language=request["language"],
            pip_packages=request["pip_packages"],
            file_ids=request["file_ids"],
        )
    except SafeToolExecutionError:
        # Preserve deliberately classified safe errors so every provider
        # adapter can transport their stable code and retry policy to the model.
        raise
    except _CodeExecutionServiceUnavailableError as exec_exc:
        logger.warning("Code execution service unavailable for tool %s: %s", tool_name, exec_exc)
        raise ValueError(str(exec_exc)) from exec_exc
    except Exception as exec_exc:
        logger.exception("Code execution failed for tool %s", tool_name)
        raise ValueError(f"Code execution failed: {exec_exc}") from exec_exc

    tool_result = exec_result.get("result") if isinstance(exec_result, dict) else {}
    if not isinstance(tool_result, dict):
        tool_result = {}
    tool_meta_keys = {
        "execution_id",
        "language",
        "service_connection",
        "error_type",
        "execution_succeeded",
        "execution_error",
        "tool_transport_succeeded",
        "execution_time",
        "timed_out",
        "files_generated",
        "input_files_loaded",
        "environment_reset",
    }
    tool_meta = {
        "code_execution": True,
        **{key: value for key, value in tool_result.items() if key in tool_meta_keys},
    }

    return {
        "tool_type": resolved_tool_type,
        "service_available": True,
        "exec_result": exec_result,
        "tool_meta": tool_meta,
        "content": format_code_execution_tool_output(
            exec_result,
            include_file_ids=include_file_ids,
        ),
    }
