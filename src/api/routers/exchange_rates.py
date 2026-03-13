from __future__ import annotations

import asyncio
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.exchange_rates import (
    ExchangeRateDTO,
    convert,
    list_rates,
    set_rate,
)

router = APIRouter(prefix="/v1", tags=["exchange-rates"])


class SetRateRequest(BaseModel):
    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    rate: float = Field(gt=0)


class ConvertResponse(BaseModel):
    from_currency: str
    to_currency: str
    original_amount: str
    converted_amount: str
    rate: str


@router.get("/exchange-rates", response_model=list[ExchangeRateDTO])
def list_exchange_rates(
    base: str = Query(default="BRL", max_length=3),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    return asyncio.run(list_rates(db, base_currency=base.upper()))


@router.post("/exchange-rates", response_model=ExchangeRateDTO)
def create_or_update_rate(
    req: SetRateRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return asyncio.run(
        set_rate(db, req.from_currency.upper(), req.to_currency.upper(), Decimal(str(req.rate)))
    )


@router.get("/exchange-rates/convert", response_model=ConvertResponse)
def convert_amount(
    amount: float = Query(gt=0),
    from_currency: str = Query(alias="from", min_length=3, max_length=3),
    to_currency: str = Query(alias="to", min_length=3, max_length=3),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    from src.application.exchange_rates import get_rate

    dec_amount = Decimal(str(amount))
    converted = asyncio.run(convert(db, dec_amount, from_currency.upper(), to_currency.upper()))
    rate = asyncio.run(get_rate(db, from_currency.upper(), to_currency.upper()))
    return ConvertResponse(
        from_currency=from_currency.upper(),
        to_currency=to_currency.upper(),
        original_amount=str(dec_amount),
        converted_amount=str(converted),
        rate=str(rate),
    )
