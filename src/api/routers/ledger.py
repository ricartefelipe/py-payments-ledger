from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.ledger import LedgerEntryDTO, list_ledger_entries

router = APIRouter(prefix="/v1", tags=["ledger"])


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


@router.get("/ledger/entries", response_model=list[LedgerEntryDTO])
def list_entries(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    return list_ledger_entries(db, tenant_id, _parse_dt(from_), _parse_dt(to))
