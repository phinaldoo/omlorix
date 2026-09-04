import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.memories import service as memory_service
from app.memories.schemas import (
    MAX_MEMORY_IMPORT_ITEMS,
    MemoryExportData,
    MemoryExportItem,
    MemoryExportPayload,
    MemoryImportItem,
)


def _export_payload(memory_count: int) -> dict:
    return {
        "export_type": "memories",
        "export_version": memory_service.CURRENT_MEMORIES_EXPORT_VERSION,
        "data": {
            "memories": [
                {"content": f"memory {index}"} for index in range(memory_count)
            ],
        },
    }


def test_memory_export_payload_enforces_account_fact_cap():
    with pytest.raises(ValidationError):
        MemoryExportPayload.model_validate(
            _export_payload(MAX_MEMORY_IMPORT_ITEMS + 1)
        )


def test_memory_export_payload_accepts_imports_at_limit():
    payload = MemoryExportPayload.model_validate(
        _export_payload(MAX_MEMORY_IMPORT_ITEMS)
    )

    assert len(payload.data.memories) == MAX_MEMORY_IMPORT_ITEMS


@pytest.mark.parametrize("version", [None, 0.9, 3.0, "1.0"])
def test_memory_export_schema_rejects_unsupported_version_shapes(version):
    payload = _export_payload(0)
    payload["export_version"] = version

    with pytest.raises(ValidationError):
        MemoryExportPayload.model_validate(payload)


@pytest.mark.parametrize("version", [1.0, 2.0])
def test_memory_export_schema_accepts_supported_versions(version):
    payload = _export_payload(1)
    payload["export_version"] = version

    parsed = MemoryExportPayload.model_validate(payload)

    assert parsed.export_version == version


@pytest.mark.parametrize("version", [0.9, 3.0])
def test_memory_import_rejects_unsupported_export_versions(version):
    payload = MemoryExportPayload.model_construct(
        export_type="memories",
        export_version=version,
        data=MemoryExportData.model_construct(
            memories=[],
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        memory_service.import_memory_export(
            object(),
            memory_service.MemoryScope.personal("user-1"),
            payload,
        )

    assert exc_info.value.status_code == 400
    assert "Expected one of [1.0, 2.0]" in exc_info.value.detail


def test_memory_import_rechecks_limit_before_database_work():
    payload = MemoryExportPayload.model_construct(
        export_type="memories",
        export_version=memory_service.CURRENT_MEMORIES_EXPORT_VERSION,
        data=MemoryExportData.model_construct(
            memories=[
                MemoryExportItem(content=f"memory {index}")
                for index in range(MAX_MEMORY_IMPORT_ITEMS + 1)
            ],
        ),
    )

    with patch.object(memory_service, "_scope_query") as scope_query:
        with pytest.raises(HTTPException) as exc_info:
            memory_service.import_memory_export(
                object(),
                memory_service.MemoryScope.personal("user-1"),
                payload,
            )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == f"You can import up to {MAX_MEMORY_IMPORT_ITEMS} memories at once"
    )
    scope_query.assert_not_called()


def test_memory_batch_import_dedupes_and_commits_once():
    db = MagicMock()
    scoped_query = db.query.return_value.filter.return_value
    filtered_query = scoped_query.filter.return_value
    query_count = 0

    def fetch_rows():
        nonlocal query_count
        query_count += 1
        if query_count == 1:
            return []
        return [call.args[0] for call in db.add.call_args_list]

    filtered_query.all.side_effect = fetch_rows
    entries = [
        MemoryImportItem(date="unknown", content="Prefers concise answers"),
        MemoryImportItem(date="unknown", content="  prefers   concise answers "),
        MemoryImportItem(date="2026-08-16", content="Uses Python"),
    ]

    result = memory_service.import_memories(
        db,
        memory_service.MemoryScope.personal("user-1"),
        entries,
    )

    assert result["created_count"] == 2
    assert result["deduped_count"] == 1
    assert db.add.call_count == 2
    db.commit.assert_called_once_with()
