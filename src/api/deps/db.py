from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from src.infrastructure.db.session import get_session


def get_db(_: Request) -> Generator[Session, None, None]:
    yield from get_session()
