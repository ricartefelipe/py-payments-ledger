from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.ledger import (
    AccountBalanceDTO,
    ConsolidatedBalanceDTO,
    LedgerEntryDTO,
    get_ledger_balances,
    get_ledger_balances_consolidated,
    list_ledger_entries,
)

router = APIRouter(prefix="/v1", tags=["ledger"])


def _parse_dt(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    if len(value) == 10:
        parsed_date = datetime.fromisoformat(value).date()
        boundary = time.max if end_of_day else time.min
        return datetime.combine(parsed_date, boundary)
    return datetime.fromisoformat(value)


@router.get("/ledger/entries", response_model=list[LedgerEntryDTO])
def list_entries(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    currency: Optional[str] = Query(default=None, max_length=3),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    return list_ledger_entries(
        db,
        tenant_id,
        _parse_dt(from_),
        _parse_dt(to, end_of_day=True),
        currency=currency,
        limit=limit,
        offset=offset,
    )


@router.get("/ledger/balances", response_model=list[AccountBalanceDTO])
def balances(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    currency: Optional[str] = Query(default=None, max_length=3),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    return get_ledger_balances(
        db,
        tenant_id,
        _parse_dt(from_),
        _parse_dt(to, end_of_day=True),
        currency=currency,
    )


@router.get("/ledger/balances/consolidated", response_model=list[ConsolidatedBalanceDTO])
def balances_consolidated(
    target_currency: str = Query(default="BRL", max_length=3),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    return get_ledger_balances_consolidated(db, tenant_id, target_currency=target_currency.upper())
