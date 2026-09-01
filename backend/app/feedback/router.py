from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user, verified_admin
from app.feedback.models import (
    delete_feedback_for_last_days,
    get_admin_feedback_by_model,
    get_admin_feedback_list,
    get_admin_feedback_overview,
    get_admin_feedback_timeline,
)
from app.feedback.schemas import FeedbackReactionRequest
from app.feedback.utils import (
    delete_feedback_by_id_with_audit_context,
    export_model_feedback,
    has_meaningful_feedback_comment,
    submit_feedback_reaction,
)
from app.logging.models import create_audit_log, get_audit_request_ip
from app.users.models import User


feedback_router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


def _audit_feedback_event(
    *,
    db_log: Session,
    request: Request,
    db: Session,
    user_id: str,
    action: str,
    details: dict | None = None,
) -> None:
    """Write one consistently attributed model-feedback audit event.

    Callers must pass metadata only. In particular, feedback comments and user
    email addresses must not be duplicated into the audit store.
    """

    create_audit_log(
        db_log=db_log,
        user_id=user_id,
        action=action,
        details=details or {},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="model_feedback",
    )


# -------------------
# React To Message
# -------------------
@feedback_router.post("/react")
def react_to_message(
    payload: FeedbackReactionRequest,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user: User = Depends(verified_user),
):
    result = submit_feedback_reaction(user.id, payload, db)

    # Record that a comment exists, but never copy its free-form contents into
    # the longer-lived audit database.
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=user.id,
        action="SUBMIT_MODEL_FEEDBACK",
        details={
            "feedback_id": result.id,
            "model_id": result.model_id,
            "message_id": result.message_id,
            "reaction": result.reaction.value,
            "has_comment": has_meaningful_feedback_comment(result.comment),
        },
    )
    return result


# ===================
# Admin Endpoints
# ===================


# -------------------
# Admin: Get Feedback Overview
# -------------------
@feedback_router.get("/admin/overview")
def admin_feedback_overview(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result = get_admin_feedback_overview(db, days=days)
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="VIEW_MODEL_FEEDBACK_OVERVIEW",
        details={"days": days, "total_feedback": result.get("total_feedback", 0)},
    )
    return result


# -------------------
# Admin: Get Feedback By Model
# -------------------
@feedback_router.get("/admin/by-model")
def admin_feedback_by_model(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result = get_admin_feedback_by_model(db, days=days)
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="VIEW_MODEL_FEEDBACK_BY_MODEL",
        details={"days": days, "model_count": len(result.get("models", []))},
    )
    return result


# -------------------
# Admin: Get Feedback Timeline
# -------------------
@feedback_router.get("/admin/timeline")
def admin_feedback_timeline(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result = get_admin_feedback_timeline(db, days=days)
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="VIEW_MODEL_FEEDBACK_TIMELINE",
        details={"days": days, "bucket_count": len(result.get("timeline", []))},
    )
    return result


# -------------------
# Admin: Get Feedback List
# -------------------
@feedback_router.get("/admin/list")
def admin_feedback_list(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    model_id: str | None = Query(None),
    reaction: str | None = Query(None),
    has_comment: bool | None = Query(None),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result = get_admin_feedback_list(
        db,
        days=days,
        page=page,
        per_page=per_page,
        model_id=model_id,
        reaction=reaction,
        has_comment=has_comment,
    )
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="LIST_MODEL_FEEDBACK",
        details={
            "days": days,
            "page": page,
            "per_page": per_page,
            "model_id": model_id,
            "reaction": reaction,
            "has_comment": has_comment,
            "returned_count": len(result.get("feedback", [])),
            "total_count": result.get("total", 0),
        },
    )
    return result


# -------------------
# Admin: Export Feedback
# -------------------
@feedback_router.get("/admin/export")
def admin_export_feedback(
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result = export_model_feedback(db)

    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="EXPORT_MODEL_FEEDBACK",
        details={
            "export_version": result.get("export_version"),
            "total_count": result.get("data", {}).get("total_count", 0),
            "sensitivity_category": "user_feedback",
        },
    )

    return result


# -------------------
# Admin: Delete Feedback
# -------------------
@feedback_router.delete("/admin/{feedback_id}")
def admin_delete_feedback(
    feedback_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result, audit_context = delete_feedback_by_id_with_audit_context(db, feedback_id)
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="DELETE_MODEL_FEEDBACK",
        details=audit_context,
    )
    return result


# -------------------
# Admin: Delete Recent Feedback
# -------------------
@feedback_router.delete("/admin")
def admin_delete_feedback_bulk(
    request: Request,
    days: int = Query(..., ge=1, le=365),
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    admin_user: User = Depends(verified_admin),
):
    result = delete_feedback_for_last_days(db, days)
    _audit_feedback_event(
        db_log=db_log,
        request=request,
        db=db,
        user_id=admin_user.id,
        action="BULK_DELETE_MODEL_FEEDBACK",
        details={
            "days": days,
            "deleted_count": result.get("deleted_count", 0),
        },
    )
    return result
