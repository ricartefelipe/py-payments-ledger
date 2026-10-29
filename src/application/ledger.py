from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import and_, case, exists, func, select
from sqlalchemy.orm import Session, joinedload

from src.infrastructure.db.models import AccountConfig, LedgerEntry, LedgerLine


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
    session: Session,
    tenant_id: str,
    from_dt: datetime | None,
    to_dt: datetime | None,
    currency: str | None = None,
    limit: int = 500,
    offset: int = 0,
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
    if currency:
        q = q.where(
            exists(
                select(LedgerLine.id).where(
                    LedgerLine.entry_id == LedgerEntry.id,
                    LedgerLine.currency == currency.upper(),
                )
            )
        )
    q = q.order_by(LedgerEntry.posted_at.desc()).offset(offset).limit(limit)
    rows = session.execute(q).unique().scalars().all()
    out: list[LedgerEntryDTO] = []
    for e in rows:
        out.append(
            LedgerEntryDTO(
                id=str(e.id),
                payment_intent_id=str(e.payment_intent_id),
                posted_at=e.posted_at.isoformat(),
                lines=[
                    LedgerLineDTO(
                        side=line.side,
                        account=line.account,
                        amount=str(line.amount),
                        currency=line.currency,
                    )
                    for line in e.lines
                ],
            )
        )
    return out


def get_ledger_balances(
    session: Session,
    tenant_id: str,
    from_dt: datetime | None,
    to_dt: datetime | None,
    currency: str | None = None,
) -> list[AccountBalanceDTO]:

    debit_sum = func.coalesce(
        func.sum(case((LedgerLine.side == "DEBIT", LedgerLine.amount), else_=Decimal(0))),
        Decimal(0),
    )
    credit_sum = func.coalesce(
        func.sum(case((LedgerLine.side == "CREDIT", LedgerLine.amount), else_=Decimal(0))),
        Decimal(0),
    )

    balance_expr = case(
        (
            AccountConfig.account_type.in_(["ASSET", "EXPENSE"]),
            debit_sum - credit_sum,
        ),
        else_=credit_sum - debit_sum,
    )

    q = (
        select(
            LedgerLine.account,
            LedgerLine.currency,
            debit_sum.label("debits_total"),
            credit_sum.label("credits_total"),
            balance_expr.label("balance"),
        )
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .outerjoin(
            AccountConfig,
            and_(
                LedgerLine.account == AccountConfig.code,
                LedgerLine.tenant_id == AccountConfig.tenant_id,
            ),
        )
        .where(LedgerEntry.tenant_id == tenant_id)
        .group_by(LedgerLine.account, LedgerLine.currency, AccountConfig.account_type)
        .order_by(LedgerLine.account, LedgerLine.currency)
    )

    if currency:
        q = q.where(LedgerLine.currency == currency.upper())
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


class ConsolidatedBalanceDTO(BaseModel):
    account: str
    target_currency: str
    balance: str
    #: Taxa usada por moeda de origem, para o numero consolidado ser auditavel.
    #: Um saldo consolidado sem a taxa nao pode ser reconferido depois.
    rates_used: dict[str, str] = {}


def get_ledger_balances_consolidated(
    session: Session,
    tenant_id: str,
    target_currency: str = "BRL",
) -> list[ConsolidatedBalanceDTO]:
    """Saldos por conta convertidos para uma moeda de apresentacao.

    Duas correcoes em relacao a versao anterior:

    * a conversao chamava ``asyncio.run`` **por linha de saldo** — um event loop
      novo para cada moeda de cada conta. Agora a taxa de cada moeda distinta e
      buscada uma unica vez e reaproveitada;
    * o resultado nao dizia qual taxa havia sido usada, o que torna um numero
      consolidado impossivel de reconferir. A taxa por moeda acompanha o DTO.

    Este e um numero de **apresentacao**: o journal continua guardando cada
    lancamento na sua propria moeda, e saldos de moedas diferentes nunca se
    compensam no livro.
    """
    from src.application.exchange_rates import get_rate

    balances = get_ledger_balances(session, tenant_id, None, None)

    # Uma consulta de taxa por moeda distinta, nao por linha.
    rates: dict[str, Decimal] = {}
    for b in balances:
        if b.currency not in rates:
            rates[b.currency] = (
                Decimal("1")
                if b.currency == target_currency
                else get_rate(session, b.currency, target_currency)
            )

    account_totals: dict[str, Decimal] = {}
    account_rates: dict[str, dict[str, str]] = {}
    for b in balances:
        rate = rates[b.currency]
        converted = (Decimal(b.balance) * rate).quantize(Decimal("0.01"))
        account_totals[b.account] = account_totals.get(b.account, Decimal("0")) + converted
        account_rates.setdefault(b.account, {})[b.currency] = str(rate)

    return [
        ConsolidatedBalanceDTO(
            account=account,
            target_currency=target_currency,
            balance=str(total.quantize(Decimal("0.01"))),
            rates_used=account_rates.get(account, {}),
        )
        for account, total in sorted(account_totals.items())
    ]
