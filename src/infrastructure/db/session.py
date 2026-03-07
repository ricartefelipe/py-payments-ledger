from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.shared.config import Settings

_ENGINE = None
_SessionLocal = None


def init_db(settings: Settings) -> None:
    global _ENGINE, _SessionLocal
    _ENGINE = create_engine(settings.database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=_ENGINE, expire_on_commit=False, class_=Session)


def get_engine():
    if _ENGINE is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    return _ENGINE


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — auto-commits on success, rollbacks on error."""
    if _SessionLocal is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    session: Session = _SessionLocal()
    try:
        yield session
        if session.in_transaction():
            session.commit()
    except Exception:
        if session.in_transaction():
            session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    session: Session = _SessionLocal()
    try:
        yield session
        if session.in_transaction():
            session.commit()
    except Exception:
        if session.in_transaction():
            session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def safe_begin(session: Session) -> Generator[Session, None, None]:
    """Begin a transaction if none is active, otherwise reuse the existing one.

    When called from FastAPI routes the session already has an implicit
    transaction (autobegin) started by middleware/auth dependencies, so we
    just yield the session.  When called from workers the session is fresh
    and we delegate to ``session.begin()`` which auto-commits on block exit.
    """
    if session.in_transaction():
        yield session
    else:
        with session.begin():
            yield session
