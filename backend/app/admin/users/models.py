"""Database operations for administrator user management."""

from app.users.models import User
from app.users.roles import ADMINISTRATIVE_ROLES
from sqlalchemy.orm import Session


def count_active_administrators(db: Session) -> int:
    """Count active administrative accounts that keep the instance manageable."""

    return (
        db.query(User.id)
        .filter(
            User.role.in_(ADMINISTRATIVE_ROLES),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .count()
    )
