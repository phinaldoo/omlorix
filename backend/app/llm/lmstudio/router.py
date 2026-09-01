import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import create_audit_log, get_audit_request_ip
from app.llm.lmstudio.utils import (
    download_model,
    list_models_all,
    list_models_loaded,
    load_model,
    unload_model,
)


lmstudio_router = APIRouter(prefix="/api/v1/llm/lmstudio", tags=["lmstudio"])


class ModelDownloadRequest(BaseModel):
    model: str
    lmstudio_provider_id: str
    quantization: str | None = None


class ModelLoadRequest(BaseModel):
    """Documented native-v1 options for loading an LM Studio model."""

    model: str
    lmstudio_provider_id: str
    context_length: int | None = None
    eval_batch_size: int | None = None
    flash_attention: bool | None = None
    num_experts: int | None = None
    offload_kv_cache_to_gpu: bool | None = None

    def build_load_config(self) -> dict[str, Any]:
        """Return only explicitly supplied LM Studio load options."""
        payload = self.model_dump()
        return {
            key: value
            for key, value in payload.items()
            if key not in {"model", "lmstudio_provider_id"} and value is not None
        }


@lmstudio_router.get("/models/all")
def models_all_route(
    lmstudio_provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    response = list_models_all(db, lmstudio_provider_id)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_LMSTUDIO_MODELS_ALL",
        details={"lmstudio_provider_id": lmstudio_provider_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_lmstudio",
    )
    return response


@lmstudio_router.get("/models/loaded")
def models_loaded_route(
    lmstudio_provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    response = list_models_loaded(lmstudio_provider_id, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_LMSTUDIO_MODELS_LOADED",
        details={"lmstudio_provider_id": lmstudio_provider_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_lmstudio",
    )
    return response


@lmstudio_router.post("/model/download")
def download_route(
    request_model: ModelDownloadRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    # Prime the generator before returning StreamingResponse. Provider lookup,
    # outbound-policy failures, and initial LM Studio HTTP errors can therefore
    # still produce their correct HTTP status instead of failing after a 200
    # streaming response has already started.
    download_stream = iter(
        download_model(
            request_model.lmstudio_provider_id,
            request_model.model,
            db,
            quantization=request_model.quantization,
        )
    )
    try:
        first_chunk = next(download_stream)
    except StopIteration as exc:
        raise HTTPException(status_code=424, detail="LM Studio did not return download status") from exc

    def primed_download_stream():
        """Yield the preflight status and then continue polling lazily."""
        yield first_chunk
        yield from download_stream

    def stream_with_audit():
        completed = False
        for chunk in primed_download_stream():
            chunk_text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            for line in chunk_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(payload.get("status") or "").strip().lower() == "completed":
                    completed = True
            yield chunk

        if completed:
            create_audit_log(
                db_log=db_log,
                user_id=admin_user.id,
                action="DOWNLOAD_LMSTUDIO_MODEL",
                details={
                    "lmstudio_provider_id": request_model.lmstudio_provider_id,
                    "model": request_model.model,
                    "quantization": request_model.quantization,
                },
                ip_address=get_audit_request_ip(request),
                user_agent=request.headers.get("user-agent"),
                category="llm_lmstudio",
            )

    return StreamingResponse(
        stream_with_audit(),
        media_type="application/x-ndjson",
    )


@lmstudio_router.post("/model/load")
def load_route(
    request_model: ModelLoadRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    load_config = request_model.build_load_config()
    response = load_model(request_model.lmstudio_provider_id, request_model.model, db, load_config=load_config)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LOAD_LMSTUDIO_MODEL",
        details={"lmstudio_provider_id": request_model.lmstudio_provider_id, "model": request_model.model, "load_config": load_config},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_lmstudio",
    )
    return response


@lmstudio_router.delete("/model/unload")
def unload_route(
    model: str,
    lmstudio_provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user=Depends(verified_admin),
):
    response = unload_model(lmstudio_provider_id, model, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UNLOAD_LMSTUDIO_MODEL",
        details={"lmstudio_provider_id": lmstudio_provider_id, "model": model},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_lmstudio",
    )
    return response
