from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from src.infrastructure.db.models import LedgerEntry, LedgerLine


class LedgerLineDTO(BaseModel):
    side: str
    account: str
    amount: str
    currency: str = "BRL"


class LedgerEntryDTO(BaseModel):
    id: str
    payment_intent_id: str
    posted_at: str
    lines: list[LedgerLineDTO]


class AccountBalanceDTO(BaseModel):
    account: str
    currency: str = "BRL"
    debits_total: str
    credits_total: str
    balance: str


def list_ledger_entries(
    session: Session, tenant_id: str, from_dt: Optional[datetime], to_dt: Optional[datetime]
) -> list[LedgerEntryDTO]:
    q = (
        select(LedgerEntry)
        .options(joinedload(LedgerEntry.lines))
        .where(LedgerEntry.tenant_id == tenant_id)
    )
    if from_dt:
        q = q.where(LedgerEntry.posted_at >= from_dt)
    if to_dt:
        q = q.where(LedgerEntry.posted_at <= to_dt)
    q = q.order_by(LedgerEntry.posted_at.desc()).limit(200)
    rows = session.execute(q).scalars().all()
    out: list[LedgerEntryDTO] = []
    for e in rows:
        out.append(
            LedgerEntryDTO(
                id=str(e.id),
                payment_intent_id=str(e.payment_intent_id),
                posted_at=e.posted_at.isoformat(),
                lines=[
                    LedgerLineDTO(side=line.side, account=line.account, amount=str(line.amount), currency=line.currency)
                    for line in e.lines
                ],
            )
        )
    return out


def get_ledger_balances(
    session: Session, tenant_id: str, from_dt: Optional[datetime], to_dt: Optional[datetime]
) -> list[AccountBalanceDTO]:
    
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

    if from_dt:
        q = q.where(LedgerEntry.posted_at >= from_dt)
    if to_dt:
        q = q.where(LedgerEntry.posted_at <= to_dt)

    rows = session.execute(q).all()
    return [
        AccountBalanceDTO(
            account=row.account,
            currency=row.currency,
            debits_total=str(row.debits_total),
            credits_total=str(row.credits_total),
            balance=str(row.balance),
        )
        for row in rows
    ]
