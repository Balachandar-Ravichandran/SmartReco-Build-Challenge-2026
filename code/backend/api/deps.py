"""FastAPI dependencies."""
from sqlalchemy.orm import Session

from backend.db.session import get_session_factory


def get_db():
    """FastAPI dependency — yields a session, commits on success, rolls back on error."""
    db: Session = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
