"""Request and response schemas for delegated group management."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

__all__ = [
    "CreateTemporaryAccountsPayload",
    "GroupMemberPromotionResult",
    "GroupPromotionCandidate",
    "GroupPromotionCandidatePage",
    "PromoteGroupMemberPayload",
    "PromotedGroupMember",
]


class CreateTemporaryAccountsPayload(BaseModel):
    count: int = Field(gt=0, le=100)
    expiry_hours: Optional[int] = Field(None, gt=0, le=720)


# -------------------
# Managed-group member promotion
# -------------------
class PromoteGroupMemberPayload(BaseModel):
    """Promote a direct group member to a higher delegated role."""

    user_id: str = Field(min_length=1, max_length=255)
    role: Literal["coordinator", "manager", "owner"]


class GroupPromotionCandidate(BaseModel):
    """One direct member shown in the read-only promotion selector."""

    id: str
    email: str
    first_name: str
    last_name: str
    status: str
    current_role: Literal["coordinator", "manager", "owner"] | None = None
    eligible: bool


class GroupPromotionCandidatePage(BaseModel):
    """Bounded promotion candidates for a managed group."""

    items: list[GroupPromotionCandidate]
    offset: int
    limit: int
    total: int
    has_more: bool


class PromotedGroupMember(BaseModel):
    """Safe user fields returned after a successful group-role promotion."""

    id: str
    email: str
    first_name: str
    last_name: str
    status: str


class GroupMemberPromotionResult(BaseModel):
    """The delegated role assignment created or upgraded by a promotion."""

    user: PromotedGroupMember
    role: Literal["coordinator", "manager", "owner"]
    capabilities: list[str]
    audit_logged: bool
