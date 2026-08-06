"""SQLAlchemy engine + session factory."""
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as SASession

from backend.core.config import get_settings
from backend.db.models import Base

# Columns added to an existing table after it was first created — create_all()
# only creates missing TABLES, it never ALTERs existing ones, so a dev DB
# created before this column existed needs this tiny idempotent migration
# rather than requiring a full reseed (which would discard real event/test
# history already accumulated in pathwise.db).
_ADDED_COLUMNS = [
    ("recommendation_log", "scope", "TEXT"),
    ("recommendation_log", "context_id", "TEXT"),
    ("recommendation_log", "solver_output_json", "TEXT"),
    ("users", "digest_enabled", "INTEGER NOT NULL DEFAULT 0"),
]


def _apply_added_columns():
    engine = get_engine()
    with engine.connect() as conn:
        for table, column, col_type in _ADDED_COLUMNS:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        # check_same_thread=False needed for SQLite + FastAPI's threaded workers
        connect_args = (
            {"check_same_thread": False}
            if settings.DATABASE_URL.startswith("sqlite")
            else {}
        )
        _engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        # expire_on_commit=False: agent nodes read ORM attributes (log.id, a
        # fetched row's columns) after their `with get_db()` block has
        # committed and closed — default expiry would force a refresh against
        # a session that's already gone, raising DetachedInstanceError.
        _SessionLocal = sessionmaker(
            bind=get_engine(), autocommit=False, autoflush=False, expire_on_commit=False
        )
    return _SessionLocal


def init_db():
    """Create all tables if they don't exist (idempotent), then apply any
    column additions to tables that already existed before those columns
    were introduced (also idempotent — checked via PRAGMA table_info)."""
    Base.metadata.create_all(bind=get_engine())
    _apply_added_columns()


@contextmanager
def get_db() -> SASession:
    """Context-managed session — commits on success, rolls back on error."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
