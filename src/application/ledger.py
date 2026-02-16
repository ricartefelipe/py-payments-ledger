from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.infrastructure.db.models import LedgerEntry


class LedgerLineDTO(BaseModel):
    side: str
    account: str
    amount: str


class LedgerEntryDTO(BaseModel):
    id: str
    payment_intent_id: str
    posted_at: str
    lines: list[LedgerLineDTO]


def list_ledger_entries(session: Session, tenant_id: str, from_dt: Optional[datetime], to_dt: Optional[datetime]) -> list[LedgerEntryDTO]:
    q = select(LedgerEntry).options(joinedload(LedgerEntry.lines)).where(LedgerEntry.tenant_id == tenant_id)
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
                lines=[LedgerLineDTO(side=l.side, account=l.account, amount=str(l.amount)) for l in e.lines],
            )
        )
    return out
