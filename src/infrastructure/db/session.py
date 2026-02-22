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
    if _SessionLocal is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    session: Session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    session: Session = _SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
