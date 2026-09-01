from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_db_log, verified_user
from app.groups.management import (
    MAX_TEMP_ACCOUNTS,
    create_temporary_accounts,
    list_group_promotion_candidates,
    managed_group_details,
    managed_groups_for_user,
    promote_group_member_with_audit_context,
    revoke_temporary_account,
    update_managed_group_settings,
)
from app.groups.schemas import (
    CreateTemporaryAccountsPayload,
    GroupMemberPromotionResult,
    GroupPromotionCandidatePage,
    PromoteGroupMemberPayload,
)
from app.logging.models import create_audit_log, get_audit_request_ip


group_management_router = APIRouter(prefix="/api/v1/group-management", tags=["group-management"])
logger = logging.getLogger(__name__)


@group_management_router.get("/groups")
def managed_groups_route(db: Session = Depends(get_db), user=Depends(verified_user)):
    return {"groups": managed_groups_for_user(db, user)}


@group_management_router.get("/groups/{group_id}")
def managed_group_details_route(
    group_id: str,
    manager_offset: int = Query(0, ge=0),
    member_offset: int = Query(0, ge=0),
    temporary_offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Return paginated manager, member, and temporary-account details."""

    return managed_group_details(
        db,
        user,
        group_id,
        manager_offset=manager_offset,
        member_offset=member_offset,
        temporary_offset=temporary_offset,
        limit=limit,
    )


@group_management_router.put("/groups/{group_id}/settings")
def managed_group_settings_route(
    group_id: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = update_managed_group_settings(
        db,
        user,
        group_id,
        settings=payload.get("settings"),
    )
    create_audit_log(
        db_log=db_log,
        user_id=user.id,
        action="MANAGE_GROUP_SETTINGS",
        details={"group_id": group_id},
        ip_address=get_audit_request_ip(request, db),
        user_agent=request.headers.get("user-agent"),
        category="group",
    )
    return result


@group_management_router.get(
    "/groups/{group_id}/manager-candidates",
    response_model=GroupPromotionCandidatePage,
)
def managed_group_manager_candidates_route(
    group_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user=Depends(verified_user),
):
    """Return direct members selectable for upward role promotion."""

    return list_group_promotion_candidates(
        db,
        user,
        group_id,
        offset=offset,
        limit=limit,
    )


@group_management_router.post(
    "/groups/{group_id}/manager-promotions",
    response_model=GroupMemberPromotionResult,
)
def managed_group_promote_member_route(
    group_id: str,
    payload: PromoteGroupMemberPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result, audit_context = promote_group_member_with_audit_context(
        db,
        user,
        group_id,
        payload.user_id,
        payload.role,
    )
    try:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action=audit_context["action"],
            details=audit_context["details"],
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="group",
        )
        result["audit_logged"] = True
    except Exception:
        # The role assignment has already committed in the main database.
        # Surface audit degradation without encouraging a duplicate promotion.
        logger.exception(
            "Group member was promoted but the audit event could not be stored",
            extra={
                "group_id": group_id,
                "target_user_id": payload.user_id,
                "acting_user_id": user.id,
            },
        )
        result["audit_logged"] = False
    return result


@group_management_router.post("/groups/{group_id}/temporary-accounts")
def managed_group_create_temporary_accounts_route(
    group_id: str,
    payload: CreateTemporaryAccountsPayload,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    try:
        count = int(payload.count)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Count must be a valid integer")
    if count < 1 or count > MAX_TEMP_ACCOUNTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Count must be between 1 and {MAX_TEMP_ACCOUNTS}",
        )
    result = create_temporary_accounts(
        db,
        user,
        group_id,
        count=count,
        expiry_hours=payload.expiry_hours,
    )
    try:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="CREATE_TEMPORARY_GROUP_ACCOUNTS",
            details={"group_id": group_id, "count": len(result.get("created", []))},
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="group",
        )
        result["audit_logged"] = True
    except Exception:
        # The audit store is a separate database and cannot participate in the
        # account transaction. Never turn a successful creation into an error
        # that hides the one-time passwords from the caller.
        logger.exception(
            "Temporary accounts were created but their audit event could not be stored",
            extra={"group_id": group_id, "acting_user_id": user.id},
        )
        result["audit_logged"] = False
    return result


@group_management_router.delete("/temporary-accounts/{account_user_id}")
def managed_group_revoke_temporary_account_route(
    account_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    db_log: Session = Depends(get_db_log),
    user=Depends(verified_user),
):
    result = revoke_temporary_account(db, user, account_user_id)
    try:
        create_audit_log(
            db_log=db_log,
            user_id=user.id,
            action="REVOKE_TEMPORARY_GROUP_ACCOUNT",
            details={
                "account_user_id": account_user_id,
                "group_id": result.get("group_id"),
                "retention_mode": result.get("retention_mode"),
                "deletion_scheduled_for": (
                    result["deletion_scheduled_for"].isoformat()
                    if result.get("deletion_scheduled_for")
                    else None
                ),
            },
            ip_address=get_audit_request_ip(request, db),
            user_agent=request.headers.get("user-agent"),
            category="group",
        )
        result["audit_logged"] = True
    except Exception:
        # Revocation has already committed in the main database. Report audit
        # degradation without making the caller believe the account is still
        # active and encouraging an unnecessary retry.
        logger.exception(
            "Temporary account was revoked but its audit event could not be stored",
            extra={
                "account_user_id": account_user_id,
                "acting_user_id": user.id,
            },
        )
        result["audit_logged"] = False
    return result
