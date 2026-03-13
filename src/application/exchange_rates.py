from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import ExchangeRate
from src.infrastructure.db.session import safe_begin
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


class ExchangeRateDTO(BaseModel):
    id: str
    from_currency: str
    to_currency: str
    rate: str
    effective_at: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_rate(session: Session, from_currency: str, to_currency: str) -> Decimal:
    if from_currency == to_currency:
        return Decimal("1")

    row = session.execute(
        select(ExchangeRate)
        .where(
            ExchangeRate.from_currency == from_currency,
            ExchangeRate.to_currency == to_currency,
        )
        .order_by(ExchangeRate.effective_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if row:
        return Decimal(str(row.rate))

    inverse = session.execute(
        select(ExchangeRate)
        .where(
            ExchangeRate.from_currency == to_currency,
            ExchangeRate.to_currency == from_currency,
        )
        .order_by(ExchangeRate.effective_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if inverse:
        return Decimal("1") / Decimal(str(inverse.rate))

    raise http_problem(
        404,
        "Not Found",
        f"no exchange rate found for {from_currency}/{to_currency}",
        instance="/v1/exchange-rates",
    )


async def convert(
    session: Session, amount: Decimal, from_currency: str, to_currency: str
) -> Decimal:
    rate = await get_rate(session, from_currency, to_currency)
    return (amount * rate).quantize(Decimal("0.01"))


async def set_rate(
    session: Session, from_currency: str, to_currency: str, rate: Decimal
) -> ExchangeRateDTO:
    if rate <= 0:
        raise http_problem(400, "Bad Request", "rate must be > 0", instance="/v1/exchange-rates")
    if len(from_currency) != 3 or len(to_currency) != 3:
        raise http_problem(
            400,
            "Bad Request",
            "currency codes must be 3 characters",
            instance="/v1/exchange-rates",
        )

    with safe_begin(session):
        er = ExchangeRate(
            from_currency=from_currency.upper(),
            to_currency=to_currency.upper(),
            rate=rate,
            effective_at=_utcnow(),
        )
        session.add(er)
        session.flush()

    log.info(
        "exchange rate set",
        extra={
            "from_currency": from_currency,
            "to_currency": to_currency,
            "rate": str(rate),
        },
    )

    return ExchangeRateDTO(
        id=str(er.id),
        from_currency=er.from_currency,
        to_currency=er.to_currency,
        rate=str(er.rate),
        effective_at=er.effective_at.isoformat(),
    )


async def list_rates(session: Session, base_currency: str = "BRL") -> list[ExchangeRateDTO]:

    subq_from = (
        select(
            ExchangeRate.from_currency,
            ExchangeRate.to_currency,
            ExchangeRate.effective_at.label("max_effective"),
        )
        .where(ExchangeRate.from_currency == base_currency)
        .distinct(ExchangeRate.from_currency, ExchangeRate.to_currency)
        .order_by(
            ExchangeRate.from_currency,
            ExchangeRate.to_currency,
            ExchangeRate.effective_at.desc(),
        )
        .subquery()
    )

    subq_to = (
        select(
            ExchangeRate.from_currency,
            ExchangeRate.to_currency,
            ExchangeRate.effective_at.label("max_effective"),
        )
        .where(ExchangeRate.to_currency == base_currency)
        .distinct(ExchangeRate.from_currency, ExchangeRate.to_currency)
        .order_by(
            ExchangeRate.from_currency,
            ExchangeRate.to_currency,
            ExchangeRate.effective_at.desc(),
        )
        .subquery()
    )

    rows_from = (
        session.execute(
            select(ExchangeRate).join(
                subq_from,
                (ExchangeRate.from_currency == subq_from.c.from_currency)
                & (ExchangeRate.to_currency == subq_from.c.to_currency)
                & (ExchangeRate.effective_at == subq_from.c.max_effective),
            )
        )
        .scalars()
        .all()
    )

    rows_to = (
        session.execute(
            select(ExchangeRate).join(
                subq_to,
                (ExchangeRate.from_currency == subq_to.c.from_currency)
                & (ExchangeRate.to_currency == subq_to.c.to_currency)
                & (ExchangeRate.effective_at == subq_to.c.max_effective),
            )
        )
        .scalars()
        .all()
    )

    seen = set()
    result: list[ExchangeRateDTO] = []
    for row in rows_from + rows_to:
        key = (row.from_currency, row.to_currency)
        if key not in seen:
            seen.add(key)
            result.append(
                ExchangeRateDTO(
                    id=str(row.id),
                    from_currency=row.from_currency,
                    to_currency=row.to_currency,
                    rate=str(row.rate),
                    effective_at=row.effective_at.isoformat(),
                )
            )
    return result
