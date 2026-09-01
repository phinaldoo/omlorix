from fastapi import APIRouter, Depends, Request
from app.llm.ollama.utils import (
    list_models_all,
    list_models_loaded,
    download_model,
    delete_model,
    load_model,
    unload_model,
    check_ollama_version,
)
from app.llm.ollama.schemas import OllamaModelActionRequest
from fastapi.responses import StreamingResponse
from app.dependencies import get_db, get_db_log, verified_admin
from sqlalchemy.orm import Session
from app.logging.models import create_audit_log, get_audit_request_ip



ollama_router = APIRouter(prefix="/api/v1/llm/ollama", tags=["ollama"])

@ollama_router.get("/models/all")
def models_route(
    ollama_provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Models route."""
    response = list_models_all(db, ollama_provider_id)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_OLLAMA_MODELS_ALL",
        details={
            "ollama_provider_id": ollama_provider_id,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return response


@ollama_router.get("/models/loaded")
def loaded(
    ollama_provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Loaded models route."""
    response = list_models_loaded(ollama_provider_id, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LIST_OLLAMA_MODELS_LOADED",
        details={
            "ollama_provider_id": ollama_provider_id,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return response


@ollama_router.post("/model/download")
def download(
    payload: OllamaModelActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Download model route."""
    # Stream NDJSON progress lines
    response = StreamingResponse(
        download_model(payload.ollama_provider_id, payload.model, db),
        media_type="application/x-ndjson"
    )
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DOWNLOAD_OLLAMA_MODEL",
        details={
            "ollama_provider_id": payload.ollama_provider_id,
            "model": payload.model,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return response


@ollama_router.delete("/model")
def delete(
    payload: OllamaModelActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Delete model route."""
    result = delete_model(payload.ollama_provider_id, payload.model, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="DELETE_OLLAMA_MODEL",
        details={
            "ollama_provider_id": payload.ollama_provider_id,
            "model": payload.model,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return result


@ollama_router.post("/model/load")
def load(
    payload: OllamaModelActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Load model route."""
    result = load_model(payload.ollama_provider_id, payload.model, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="LOAD_OLLAMA_MODEL",
        details={
            "ollama_provider_id": payload.ollama_provider_id,
            "model": payload.model,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return result


@ollama_router.post("/model/unload")
def unload(
    payload: OllamaModelActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Unload model route."""
    result = unload_model(payload.ollama_provider_id, payload.model, db)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="UNLOAD_OLLAMA_MODEL",
        details={
            "ollama_provider_id": payload.ollama_provider_id,
            "model": payload.model,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return result


@ollama_router.get("/version")
def version(
    ollama_provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user = Depends(verified_admin),
):
    """Version route."""
    result = check_ollama_version(db, ollama_provider_id)
    create_audit_log(
        db_log=db_log,
        user_id=admin_user.id,
        action="CHECK_OLLAMA_VERSION",
        details={
            "ollama_provider_id": ollama_provider_id,
        },
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="llm_ollama",
    )
    return result
