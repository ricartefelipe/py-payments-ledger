from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.infrastructure.db.models import LedgerEntry, LedgerLine


router = APIRouter(prefix="/v1", tags=["reports"])


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


class RevenueReportItem(BaseModel):
    period: str
    currency: str
    total: str


class TenantRevenueItem(BaseModel):
    tenant_id: str
    currency: str
    total: str


class AccountBalanceReportItem(BaseModel):
    account: str
    currency: str
    debits_total: str
    credits_total: str
    balance: str


@router.get("/reports/revenue", response_model=list[RevenueReportItem])
def revenue_by_period(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    granularity: str = Query(default="month", regex="^(day|week|month)$"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    trunc_map = {"day": "day", "week": "week", "month": "month"}
    trunc = trunc_map[granularity]

    period_col = func.date_trunc(trunc, LedgerEntry.posted_at).label("period")
    total_col = func.coalesce(func.sum(LedgerLine.amount), 0).label("total")

    q = (
        select(period_col, LedgerLine.currency, total_col)
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(
            LedgerEntry.tenant_id == tenant_id,
            LedgerLine.side == "CREDIT",
            LedgerLine.account == "REVENUE",
        )
        .group_by(period_col, LedgerLine.currency)
        .order_by(period_col)
    )

    from_dt, to_dt = _parse_dt(from_), _parse_dt(to)
    if from_dt:
        q = q.where(LedgerEntry.posted_at >= from_dt)
    if to_dt:
        q = q.where(LedgerEntry.posted_at <= to_dt)

    rows = db.execute(q).all()
    return [
        RevenueReportItem(
            period=row.period.isoformat() if row.period else "",
            currency=row.currency,
            total=str(row.total),
        )
        for row in rows
    ]


@router.get("/reports/account-balances", response_model=list[AccountBalanceReportItem])
def account_balances_report(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    debit_sum = func.coalesce(
        func.sum(case((LedgerLine.side == "DEBIT", LedgerLine.amount), else_=Decimal(0))),
        Decimal(0),
    )
    credit_sum = func.coalesce(
        func.sum(case((LedgerLine.side == "CREDIT", LedgerLine.amount), else_=Decimal(0))),
        Decimal(0),
    )

    q = (
        select(
            LedgerLine.account,
            LedgerLine.currency,
            debit_sum.label("debits_total"),
            credit_sum.label("credits_total"),
            (credit_sum - debit_sum).label("balance"),
        )
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(LedgerEntry.tenant_id == tenant_id)
        .group_by(LedgerLine.account, LedgerLine.currency)
        .order_by(LedgerLine.account, LedgerLine.currency)
    )

    from_dt, to_dt = _parse_dt(from_), _parse_dt(to)
    if from_dt:
        q = q.where(LedgerEntry.posted_at >= from_dt)
    if to_dt:
        q = q.where(LedgerEntry.posted_at <= to_dt)

    rows = db.execute(q).all()
    return [
        AccountBalanceReportItem(
            account=row.account,
            currency=row.currency,
            debits_total=str(row.debits_total),
            credits_total=str(row.credits_total),
            balance=str(row.balance),
        )
        for row in rows
    ]
