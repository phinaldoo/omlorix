import logging
from typing import Any

from app.database import SessionLocal, AuditSessionLocal


logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_log():
    db = AuditSessionLocal()
    try:
        yield db
    finally:
        db.close()


def release_db_session_before_long_wait(db: Any) -> bool:
    """Return a clean request Session's connection before long async work.

    SQLAlchemy Sessions remain reusable after a clean commit. Refuse to move
    the transaction boundary when ORM writes are pending so a performance
    optimization can never discard or unexpectedly commit business data.
    """

    if db is None:
        return False
    try:
        if any(bool(getattr(db, name, ())) for name in ("new", "dirty", "deleted")):
            logger.warning(
                "Retaining database session across long I/O because it has pending changes"
            )
            return False

        in_transaction = getattr(db, "in_transaction", None)
        if callable(in_transaction) and not in_transaction():
            return True

        commit = getattr(db, "commit", None)
        if callable(commit):
            commit()
            return True

        close = getattr(db, "close", None)
        if callable(close):
            close()
            return True
    except Exception:
        logger.exception("Could not release a database session before long I/O")
    return False
