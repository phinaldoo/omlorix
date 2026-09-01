from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import base64
import json

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.chats.compliance import ComplianceWatermarkResolver
from app.chats.models import Chats, ChatMessages
from app.chats.download import export_chat_full, current_chat_export_version
from app.chats.export_security import is_chat_excluded_from_default_export
from app.chats.schemas import (
    CHAT_IMPORT_MAX_DEEP_RESEARCH_BYTES_PER_CHAT,
    ChatExportImportPayload,
    ImportedChatEntry,
)
from app.users.models import User
from app.utils.email import normalize_email, build_email_reference_token


_IMPORT_STRIPPED_CHAT_META_KEYS = {"code_execution"}
_IMPORT_STRIPPED_OPENAI_CONTINUATION_META_KEYS = {
    "response_id",
    "continuation_fingerprint",
    "continuation_signature",
}


def _sanitize_import_chat_meta(meta: dict) -> dict:
    sanitized = dict(meta)
    for key in _IMPORT_STRIPPED_CHAT_META_KEYS:
        sanitized.pop(key, None)
    return sanitized


def _strip_imported_openai_continuation_metadata(value: Any) -> Any:
    """Remove provider-side continuation capabilities from imported messages.

    Imports always receive a new local chat ID, so a stored OpenAI response ID
    cannot legitimately remain bound to the destination conversation. Other
    display and usage metadata is preserved unchanged.
    """

    if isinstance(value, list):
        return [_strip_imported_openai_continuation_metadata(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key == "meta" and isinstance(item, dict):
            cleaned[key] = _sanitize_imported_openai_continuation_meta(item)
        else:
            cleaned[key] = _strip_imported_openai_continuation_metadata(item)
    return cleaned


def _sanitize_imported_openai_continuation_meta(meta: Any) -> Any:
    """Strip continuation keys from a value known to be a metadata object."""

    if not isinstance(meta, dict):
        return meta
    cleaned_meta = dict(meta)
    # ``response_id`` is otherwise a generic metadata name, so remove it only
    # when the OpenAI continuation markers identify the object as a stored-
    # response capability. The markers themselves are always import-unsafe.
    is_openai_continuation = any(
        key in cleaned_meta
        for key in _IMPORT_STRIPPED_OPENAI_CONTINUATION_META_KEYS
        if key != "response_id"
    )
    for metadata_key in _IMPORT_STRIPPED_OPENAI_CONTINUATION_META_KEYS:
        if metadata_key != "response_id":
            cleaned_meta.pop(metadata_key, None)
    if is_openai_continuation:
        cleaned_meta.pop("response_id", None)
    return _strip_imported_openai_continuation_metadata(cleaned_meta)


def _deep_research_import_run_id_map(rows: Any) -> dict[str, str]:
    """Allocate stable destination IDs before importing messages or runs."""

    if not isinstance(rows, list):
        return {}
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_run_id = str(row.get("id") or "").strip()
        if source_run_id and source_run_id not in mapping:
            mapping[source_run_id] = str(uuid4())
    return mapping


def _remap_deep_research_run_references(
    value: Any,
    run_id_map: dict[str, str],
) -> Any:
    """Rewrite imported widget metadata, HTML attributes, and artifact URLs.

    Source identifiers are not assumed to be UUIDs because portable imports can
    originate from older or third-party producers. Consequently, ordinary prose
    must not be changed merely because it contains a short source identifier.
    """

    if not run_id_map:
        return value
    if isinstance(value, dict):
        return {
            key: _remap_deep_research_run_references(item, run_id_map)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _remap_deep_research_run_references(item, run_id_map)
            for item in value
        ]
    if not isinstance(value, str):
        return value

    if value in run_id_map:
        return run_id_map[value]

    # Message content is commonly a JSON-encoded block list. Decode that outer
    # layer so exact run-id values inside metadata can be changed without doing
    # an unsafe substring replacement across user-authored text.
    stripped = value.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            remapped_json = _remap_deep_research_run_references(
                decoded,
                run_id_map,
            )
            if remapped_json != decoded:
                return json.dumps(remapped_json)

    remapped = value
    for source_run_id, imported_run_id in run_id_map.items():
        # Widget HTML and report file links are the two non-JSON representations
        # persisted by Deep Research. Restrict replacements to their stable
        # syntax so an identifier such as "run" cannot corrupt normal prose.
        for attribute in ("data-widget-id", "data-run-id", "data-session-id"):
            remapped = remapped.replace(
                f'{attribute}="{source_run_id}"',
                f'{attribute}="{imported_run_id}"',
            )
            remapped = remapped.replace(
                f"{attribute}='{source_run_id}'",
                f"{attribute}='{imported_run_id}'",
            )
        remapped = remapped.replace(
            f"/api/v1/deep-research/runs/{source_run_id}/files/",
            f"/api/v1/deep-research/runs/{imported_run_id}/files/",
        )
        remapped = remapped.replace(
            f"Run ID: {source_run_id}",
            f"Run ID: {imported_run_id}",
        )
    return remapped


def _remap_portable_archive_references(value: Any, id_map: dict[str, str]) -> Any:
    """Rewrite exact portable identifiers, including JSON-encoded metadata.

    Exact-value matching deliberately avoids replacing identifiers embedded in
    user-authored prose.  Chat attachment arrays and project metadata can be
    stored as JSON strings, so those containers are decoded and re-encoded.
    """
    if not id_map:
        return value
    if isinstance(value, dict):
        return {
            key: _remap_portable_archive_references(item, id_map)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_remap_portable_archive_references(item, id_map) for item in value]
    if not isinstance(value, str):
        return value
    if value in id_map:
        return id_map[value]
    stripped = value.lstrip()
    if not stripped.startswith(("[", "{")):
        return value
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value
    if not isinstance(decoded, (dict, list)):
        return value
    remapped = _remap_portable_archive_references(decoded, id_map)
    return json.dumps(remapped) if remapped != decoded else value


def _format_import_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg") or "Invalid value."
        parts.append(f"{location}: {message}" if location else str(message))
    return "; ".join(parts) if parts else "Invalid import payload."


def export_user_chats_payload(user_id: str, db: Session, include_deleted_or_temp: bool = False) -> dict:
    """Return a versioned export payload containing all chats for *user_id*."""
    chat_rows = (
        db.query(Chats)
        .filter(Chats.user_id == user_id)
        .order_by(Chats.created_at.asc(), Chats.id.asc())
        .all()
    )

    items: list[dict[str, Any]] = []
    watermark_resolver = ComplianceWatermarkResolver(db)
    for chat in chat_rows:
        if not include_deleted_or_temp and is_chat_excluded_from_default_export(chat):
            continue
        watermark = watermark_resolver.for_user(user_id)
        # Keep policy resolution in this request-scoped resolver so one group
        # lookup covers every chat in a self or user-data export.
        export_kwargs: dict[str, Any] = {
            "include_deleted_or_temp": include_deleted_or_temp,
        }
        if watermark:
            export_kwargs["compliance_watermark"] = watermark
        items.append(export_chat_full(user_id, chat.id, db, **export_kwargs))

    return {
        "export_type": "chats",
        "export_version": current_chat_export_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "chats": items,
            "count": len(items),
        },
    }


def import_user_chats_payload(user_id: str, payload: dict, db: Session) -> dict:
    """Import chats for user_id from an export payload."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload. Expected an object.")

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "chats":
        raise HTTPException(status_code=400, detail="Unsupported export_type for chat import.")
    if export_version != current_chat_export_version:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export_version '{export_version}'. Expected '{current_chat_export_version}'.",
        )

    # Omlorix has not shipped a public export format yet, so imports only need
    # to support the single current wrapper. Rejecting the old unversioned
    # single-chat shape keeps validation deterministic for the 1.0 contract.
    try:
        validated_payload = ChatExportImportPayload.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid import payload. {_format_import_validation_error(exc)}",
        ) from exc
    chats_block = validated_payload.data.chats

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    existing_source_chat_ids = _collect_existing_chat_source_ids(db, user_id)

    for index, chat_entry in enumerate(chats_block):
        if not isinstance(chat_entry, dict):
            errors.append({"index": index, "error": "Chat entry must be an object."})
            continue

        try:
            validated_entry = ImportedChatEntry.model_validate(chat_entry)
            normalized_entry = validated_entry.model_dump(mode="json")
        except ValidationError as exc:
            errors.append({"index": index, "error": _format_import_validation_error(exc)})
            continue

        source_chat_id = _extract_source_chat_id(normalized_entry)
        if source_chat_id and source_chat_id in existing_source_chat_ids:
            chat_data = normalized_entry.get("chat") or {}
            skipped.append(
                {
                    "index": index,
                    "source_chat_id": source_chat_id,
                    "title": chat_data.get("title"),
                }
            )
            continue

        try:
            with db.begin_nested():
                chat_summary = _import_single_chat(user_id, normalized_entry, db)
                # Flush inside the savepoint so constraint/DB failures stay scoped to this chat.
                db.flush()
            created.append({"index": index, **chat_summary})
            if source_chat_id:
                existing_source_chat_ids.add(source_chat_id)
        except HTTPException as exc:
            errors.append({"index": index, "error": exc.detail})
        except Exception as exc:  # pylint: disable=broad-except
            errors.append({"index": index, "error": str(exc)})

    db.commit()
    return {
        "created_count": len(created),
        "created_message_count": sum(int(item.get("message_count") or 0) for item in created),
        "created": created,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "warnings": [],
        "errors": errors,
    }


def import_all_chats_payload(payload: dict, db: Session) -> dict:
    """Import an all-users chat export by resolving each chat to a local user."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload. Expected an object.")

    try:
        validated_payload = ChatExportImportPayload.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid import payload. {_format_import_validation_error(exc)}",
        ) from exc

    export_type = payload.get("export_type")
    export_version = payload.get("export_version")
    if export_type != "chats":
        raise HTTPException(status_code=400, detail="Unsupported export_type for all-chats import.")
    if export_version != current_chat_export_version:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export_version '{export_version}'. Expected '{current_chat_export_version}'.",
        )

    chats_block = validated_payload.data.chats
    user_reference_map = _normalize_user_reference_map(validated_payload.data.user_reference_map)
    user_reference_lookup = _build_local_user_reference_lookup(db)

    chats_by_user: dict[str, list[dict[str, Any]]] = {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, chat_entry in enumerate(chats_block):
        if not isinstance(chat_entry, dict):
            errors.append({"index": index, "error": "Chat entry must be an object."})
            continue

        try:
            validated_entry = ImportedChatEntry.model_validate(chat_entry)
            normalized_entry = validated_entry.model_dump(mode="json")
        except ValidationError as exc:
            errors.append({"index": index, "error": _format_import_validation_error(exc)})
            continue

        target_user = _resolve_target_user_for_chat_entry(normalized_entry, user_reference_map, user_reference_lookup)
        if not target_user:
            chat_data = normalized_entry.get("chat") or {}
            warnings.append(
                {
                    "index": index,
                    "source_user_id": chat_data.get("user_id"),
                    "source_chat_id": chat_data.get("id"),
                    "warning": "Import skipped because referenced user was not found",
                }
            )
            continue

        chats_by_user.setdefault(target_user.id, []).append(normalized_entry)

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for user_id, user_chats in chats_by_user.items():
        result = import_user_chats_payload(
            user_id,
            {
                "export_type": "chats",
                "export_version": current_chat_export_version,
                "data": {
                    "chats": user_chats,
                    "count": len(user_chats),
                },
            },
            db,
        )
        created.extend(
            [
                {
                    "user_id": user_id,
                    **entry,
                }
                for entry in result.get("created", [])
            ]
        )
        skipped.extend(
            [
                {
                    "user_id": user_id,
                    **entry,
                }
                for entry in result.get("skipped", [])
            ]
        )
        errors.extend(
            [
                {
                    "user_id": user_id,
                    **entry,
                }
                for entry in result.get("errors", [])
            ]
        )

    return {
        "created_count": len(created),
        "created_message_count": sum(int(item.get("message_count") or 0) for item in created),
        "created": created,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "warnings": warnings,
        "errors": errors,
    }


def _import_single_chat(
    user_id: str,
    entry: dict,
    db: Session,
    *,
    project_id_map: dict[str, str] | None = None,
    file_id_map: dict[str, str] | None = None,
) -> dict:
    """Import a complete chat and optionally reconnect canonical bundle IDs."""
    chat_data = entry.get("chat") or {}
    messages = entry.get("messages") or []
    deep_research_rows = entry.get("deep_research_runs")
    deep_research_run_id_map = _deep_research_import_run_id_map(
        deep_research_rows
    )

    if not isinstance(chat_data, dict):
        raise HTTPException(status_code=400, detail="Chat entry missing 'chat' object.")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="Chat entry 'messages' must be a list.")

    created_at = _safe_datetime(chat_data.get("created_at"))
    last_updated_at = _safe_datetime(chat_data.get("last_updated_at"), created_at)
    raw_meta = chat_data.get("meta")
    chat_meta = raw_meta if isinstance(raw_meta, dict) else _safe_json(raw_meta) or {}
    chat_meta = _sanitize_import_chat_meta(chat_meta)
    source_chat_id = str(chat_data.get("id") or "").strip()
    if source_chat_id and not chat_meta.get("import_source_chat_id"):
        chat_meta["import_source_chat_id"] = source_chat_id

    chat = Chats(
        id=str(uuid4()),
        user_id=user_id,
        title=chat_data.get("title"),
        project_id=(project_id_map or {}).get(
            str(chat_data.get("project_id") or "").strip()
        ),
        share=None,
        share_id=None,
        archived=bool(chat_data.get("archived")),
        pinned_position=_safe_int(chat_data.get("pinned_position")),
        meta=chat_meta or None,
        created_at=created_at,
        last_updated_at=last_updated_at,
        # Imported content defaults to read unless the originating export
        # explicitly carried an unread response marker.
        response_version=1 if bool(chat_data.get("has_unread_response")) else 0,
    )
    db.add(chat)
    # No ORM relationship links Chats to its imported child rows, so the unit
    # of work cannot infer insert order from the scalar foreign keys alone.
    # Persist the parent inside the current transaction before staging children.
    db.flush()

    prepared_messages: list[tuple[dict[str, Any], str]] = []
    imported_message_ids_by_source_id: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg = _remap_portable_archive_references(msg, file_id_map or {})
        msg = _remap_deep_research_run_references(
            msg,
            deep_research_run_id_map,
        )
        new_message_id = str(uuid4())
        prepared_messages.append((msg, new_message_id))
        source_message_id = str(msg.get("id") or "").strip()
        if source_message_id:
            imported_message_ids_by_source_id[source_message_id] = new_message_id

    message_count = 0
    last_ts = chat.created_at
    for msg, new_message_id in prepared_messages:
        message_count += 1
        msg_created = _safe_datetime(msg.get("created_at"), fallback=last_ts)
        source_reference_id = str(msg.get("reference_id") or "").strip()
        mapped_reference_id = imported_message_ids_by_source_id.get(source_reference_id, source_reference_id or None)
        message = ChatMessages(
            id=new_message_id,
            chat_id=chat.id,
            model_id=str(msg.get("model_id") or "imported-model"),
            role=str(msg.get("role") or "user"),
            reference_id=mapped_reference_id,
            generation=_safe_json(msg.get("generation")),
            content=_build_imported_message_content(msg),
            thinking=_encode_json_field(msg.get("thinking")),
            retry_count=_safe_int(msg.get("retry_count")) or 0,
            bookmarked=bool(msg.get("bookmarked")),
            created_at=msg_created,
        )
        db.add(message)
        last_ts = msg_created or last_ts

    _import_deep_research_runs_for_chat(
        user_id,
        chat.id,
        deep_research_rows,
        db,
        run_id_map=deep_research_run_id_map,
    )
    chat.last_updated_at = last_ts or chat.last_updated_at
    return {
        "chat_id": chat.id,
        "title": chat.title,
        "message_count": message_count,
    }


def _import_deep_research_runs_for_chat(
    user_id: str,
    chat_id: str,
    rows: Any,
    db: Session,
    *,
    run_id_map: dict[str, str] | None = None,
) -> None:
    """Restore v2 run history without executing imported queued work."""

    if not isinstance(rows, list) or not rows:
        return
    from app.tools.deep_research.models import (
        DeepResearchArtifact,
        DeepResearchRun,
        TERMINAL_RUN_STATUSES,
    )
    from app.tools.deep_research.storage import (
        get_deep_research_workspace_dir,
        upload_deep_research_artifacts,
        write_workspace_bytes,
        write_workspace_text,
    )

    imported_run_ids = run_id_map or _deep_research_import_run_id_map(rows)
    restored_binary_bytes = 0
    processed_source_run_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_run_id = str(row.get("id") or "").strip()
        if not source_run_id or source_run_id in processed_source_run_ids:
            continue
        processed_source_run_ids.add(source_run_id)
        run_id = imported_run_ids.get(source_run_id) or str(uuid4())
        imported_status = str(row.get("status") or "failed").strip().lower()
        if imported_status not in TERMINAL_RUN_STATUSES:
            imported_status = "failed"
        config_snapshot = _safe_json(row.get("config_snapshot")) or {}
        # Profiles and their derived budgets are no longer part of the run
        # contract. Do not reintroduce them through an older portable export.
        config_snapshot.pop("quality_profile", None)
        config_snapshot.pop("budgets", None)
        run = DeepResearchRun(
            id=run_id,
            user_id=str(user_id),
            chat_id=str(chat_id),
            generation_id=None,
            query=str(row.get("query") or "Imported Deep Research run"),
            execution_mode=(
                str(row.get("execution_mode") or "custom").strip().lower()
                if str(row.get("execution_mode") or "custom").strip().lower()
                in {"custom", "native"}
                else "custom"
            ),
            output_format=(
                str(row.get("output_format") or "markdown").strip().lower()
                if str(row.get("output_format") or "markdown").strip().lower()
                in {"markdown", "markdown_and_html"}
                else "markdown"
            ),
            status=imported_status,
            phase=(
                str(row.get("phase") or imported_status)
                if imported_status != "failed" or row.get("status") == "failed"
                else "imported-incomplete"
            ),
            provider_id=row.get("provider_id"),
            model_id=row.get("model_id"),
            model_name=row.get("model_name"),
            prompt_version=str(row.get("prompt_version") or "v2"),
            revision_round=_safe_int(row.get("revision_round")) or 0,
            max_revision_rounds=max(
                1,
                min(_safe_int(row.get("max_revision_rounds")) or 2, 3),
            ),
            cancel_requested=bool(row.get("cancel_requested")),
            started_at=_safe_datetime(row.get("started_at")) if row.get("started_at") else None,
            completed_at=_safe_datetime(row.get("completed_at")) if row.get("completed_at") else None,
            created_at=_safe_datetime(row.get("created_at")),
            updated_at=_safe_datetime(row.get("updated_at")),
            error_code=row.get("error_code"),
            error_message_key=(
                row.get("error_message_key")
                if imported_status != "failed" or row.get("status") == "failed"
                else "deep_research_imported_incomplete"
            ),
            config_snapshot=config_snapshot,
            usage=_safe_json(row.get("usage")) or {},
            quality_gate=_safe_json(row.get("quality_gate")) or {},
            result_meta={
                **(_safe_json(row.get("result_meta")) or {}),
                "import_source_run_id": source_run_id,
            },
        )
        db.add(run)
        db.flush()

        workspace = get_deep_research_workspace_dir(str(user_id), run_id)
        file_contents = (
            row.get("file_contents")
            if isinstance(row.get("file_contents"), dict)
            else {}
        )
        restored_paths: set[str] = set()
        for relative_path, content in file_contents.items():
            if not isinstance(content, str) or len(content) > 2 * 1024 * 1024:
                continue
            try:
                write_workspace_text(workspace, str(relative_path), content)
                restored_paths.add(str(relative_path))
            except ValueError:
                continue
        artifact_contents = (
            row.get("artifact_contents")
            if isinstance(row.get("artifact_contents"), dict)
            else {}
        )
        for relative_path, encoded in artifact_contents.items():
            if (
                not isinstance(encoded, dict)
                or encoded.get("encoding") != "base64"
                or not str(relative_path).startswith("artifacts/")
            ):
                continue
            data = encoded.get("data")
            if not isinstance(data, str) or len(data) > 14 * 1024 * 1024:
                continue
            try:
                content = base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                continue
            if (
                not content
                or len(content) > 10 * 1024 * 1024
                or restored_binary_bytes + len(content)
                > CHAT_IMPORT_MAX_DEEP_RESEARCH_BYTES_PER_CHAT
            ):
                continue
            try:
                write_workspace_bytes(workspace, str(relative_path), content)
            except ValueError:
                continue
            restored_paths.add(str(relative_path))
            restored_binary_bytes += len(content)
        run.final_report_path = (
            row.get("final_report_path")
            if row.get("final_report_path") in restored_paths
            else None
        )
        run.final_html_path = (
            row.get("final_html_path")
            if row.get("final_html_path") in restored_paths
            else None
        )
        run.manifest_path = (
            row.get("manifest_path")
            if row.get("manifest_path") in restored_paths
            else None
        )

        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        seen_urls: set[str] = set()
        normalized_evidence: list[dict[str, Any]] = []
        for item in evidence[:5000]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("canonical_url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            normalized_evidence.append(
                {
                    "title": str(item.get("title") or url),
                    "canonical_url": url,
                    "provider": item.get("provider"),
                    "source_type": item.get("source_type"),
                    "published_at": item.get("published_at"),
                    "author": item.get("author"),
                    "content_hash": item.get("content_hash"),
                    "excerpt": item.get("excerpt"),
                    "research_questions": _safe_json(item.get("research_questions")) or [],
                    "meta": _safe_json(item.get("meta")) or {},
                    "created_at": str(item.get("created_at") or "") or None,
                    "updated_at": str(item.get("updated_at") or "") or None,
                }
            )
        run.evidence = normalized_evidence

        artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), list) else []
        seen_stable_ids: set[str] = set()
        normalized_artifacts: list[dict[str, Any]] = []
        for item in artifacts[:1000]:
            if not isinstance(item, dict):
                continue
            stable_id = str(item.get("stable_id") or "").strip()
            relative_path = str(item.get("relative_path") or "").strip()
            if not stable_id or not relative_path or stable_id in seen_stable_ids:
                continue
            seen_stable_ids.add(stable_id)
            normalized_artifacts.append(
                DeepResearchArtifact(
                    stable_id=stable_id,
                    file_id=None,
                    source_phase=str(item.get("source_phase") or "import"),
                    original_filename=str(item.get("original_filename") or stable_id),
                    relative_path=relative_path,
                    media_type=str(item.get("media_type") or "application/octet-stream"),
                    kind=str(item.get("kind") or "other"),
                    size_bytes=_safe_int(item.get("size_bytes")),
                    sha256=item.get("sha256"),
                    caption=item.get("caption"),
                    alt_text=item.get("alt_text"),
                    source_url=item.get("source_url"),
                    attribution=item.get("attribution"),
                    license_name=item.get("license_name"),
                    validation_status=(
                        "validated"
                        if relative_path in restored_paths
                        else "missing_import"
                    ),
                    meta={
                        **(_safe_json(item.get("meta")) or {}),
                        "import_source_run_id": source_run_id,
                    },
                    created_at=str(row.get("created_at") or "") or None,
                    updated_at=str(row.get("updated_at") or "") or None,
                ).to_dict()
            )
        run.artifacts = normalized_artifacts
        db.add(run)
        upload_deep_research_artifacts(
            workspace_dir=workspace,
            user_id=str(user_id),
            session_id=run_id,
        )


def _extract_source_chat_id(entry: dict[str, Any]) -> str | None:
    """Extract the source chat ID from an import entry dict."""
    if not isinstance(entry, dict):
        return None
    chat_data = entry.get("chat") or {}
    source_chat_id = str(chat_data.get("id") or "").strip()
    return source_chat_id or None


def _normalize_user_reference_map(raw_map: Any) -> dict[str, str]:
    """Normalize a raw user reference map into a dict of user_id to reference token/email."""
    if not isinstance(raw_map, dict):
        return {}

    normalized: dict[str, str] = {}
    for user_id, reference_value in raw_map.items():
        key = str(user_id or "").strip()
        normalized_value = _normalize_user_reference_value(reference_value)
        if not key or not normalized_value:
            continue
        normalized[key] = normalized_value
    return normalized


def _normalize_user_reference_value(value: Any) -> str | None:
    """Normalize a single user reference value (email or sha256 hash)."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower().startswith("sha256:"):
        return stripped.lower()
    return normalize_email(stripped)


def _build_local_user_reference_lookup(db: Session) -> dict[str, User]:
    """Build a lookup dict mapping normalized email and reference tokens to User objects."""
    lookup: dict[str, User] = {}
    for user in db.query(User).all():
        normalized_email = normalize_email(getattr(user, "email", None))
        if normalized_email:
            lookup[normalized_email] = user
        reference_token = build_email_reference_token(getattr(user, "email", None))
        if reference_token:
            lookup[reference_token] = user
    return lookup


def _resolve_target_user_for_chat_entry(
    entry: dict[str, Any],
    user_reference_map: dict[str, str],
    user_reference_lookup: dict[str, User],
) -> User | None:
    """Resolve the target local User for an imported chat entry."""
    chat_data = entry.get("chat") or {}
    source_user_id = str(chat_data.get("user_id") or "").strip()
    if source_user_id:
        source_reference = user_reference_map.get(source_user_id)
        if source_reference:
            return user_reference_lookup.get(source_reference)

    return None


def _collect_existing_chat_source_ids(db: Session, user_id: str) -> set[str]:
    """Collect all existing chat IDs and import source IDs for duplicate detection."""
    existing_ids: set[str] = set()
    for chat in db.query(Chats).filter(Chats.user_id == user_id).all():
        if getattr(chat, "id", None):
            existing_ids.add(str(chat.id))
        meta = getattr(chat, "meta", None)
        meta_dict = meta if isinstance(meta, dict) else _safe_json(meta) or {}
        source_chat_id = str(meta_dict.get("import_source_chat_id") or "").strip()
        if source_chat_id:
            existing_ids.add(source_chat_id)
    return existing_ids


def _build_imported_message_content(message_payload: dict[str, Any]) -> str:
    """Build the serialized content string for an imported message, handling attachments and metadata."""
    role = str(message_payload.get("role") or "user")
    raw_content = message_payload.get("content")
    parsed_content = _strip_imported_openai_continuation_metadata(
        _parse_jsonish(raw_content)
    )
    if isinstance(parsed_content, (list, dict)):
        return json.dumps(parsed_content)

    attachments = {
        "images": _normalize_list_payload(message_payload.get("images")),
        "videos": _normalize_list_payload(message_payload.get("videos")),
        "audios": _normalize_list_payload(message_payload.get("audios")),
        "documents": _normalize_list_payload(message_payload.get("documents")),
        "youtube": _normalize_list_payload(message_payload.get("youtube")),
        "sources": _normalize_list_payload(message_payload.get("sources")),
    }
    tool_name = str(message_payload.get("name") or message_payload.get("tool_name") or "").strip()
    message_meta = _sanitize_imported_openai_continuation_meta(
        _safe_json(message_payload.get("meta"))
    )

    if any(value for value in attachments.values()) or tool_name or message_meta:
        block: dict[str, Any] = {
            "type": "user" if role == "user" else "content",
            "content": "" if raw_content is None else str(raw_content),
        }
        for key, value in attachments.items():
            if value:
                block[key] = value
        if tool_name:
            block["tool_name"] = tool_name
        if message_meta:
            block["meta"] = message_meta
        return json.dumps([block])

    return "" if raw_content is None else str(raw_content)


def _normalize_list_payload(value: Any) -> list[Any] | None:
    """Normalize a value into a list, parsing JSON strings if needed. Return None for empty."""
    parsed = _parse_jsonish(value)
    if parsed is None:
        return None
    if isinstance(parsed, list):
        return parsed or None
    if isinstance(parsed, str):
        stripped = parsed.strip()
        return [stripped] if stripped else None
    return [parsed]


def _parse_jsonish(value: Any) -> Any:
    """Attempt to parse a value as JSON if it's a string, otherwise return as-is."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except Exception:
            return value
    return value


def _safe_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    """Safely convert a value to a UTC datetime (ISO string, timestamp, or datetime)."""
    if value is None:
        return fallback or datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:  # pragma: no cover - invalid timestamp
            return fallback or datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:  # pragma: no cover - malformed value
            return fallback or datetime.now(timezone.utc)
    return fallback or datetime.now(timezone.utc)


def _safe_int(value: Any) -> int | None:
    """Safely convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_json(value: Any) -> dict | None:
    """Safely parse a value as a JSON dict, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _encode_json_field(value: Any) -> str | None:
    """Encode a dict/list value as a JSON string, or convert to str."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value)
        except Exception:
            return str(value)
    return str(value)
