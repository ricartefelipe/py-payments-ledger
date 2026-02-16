from __future__ import annotations

from collections.abc import Generator
from fastapi import Request
from sqlalchemy.orm import Session

from src.infrastructure.db.session import session_scope


def get_db(_: Request) -> Generator[Session, None, None]:
    # session_scope yields and closes session
    yield from session_scope()
