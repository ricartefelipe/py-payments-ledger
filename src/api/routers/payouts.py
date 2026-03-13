from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.payouts import (
    PayoutDTO,
    cancel_payout,
    create_payout,
    get_payout,
    list_payouts,
    process_payout,
)

router = APIRouter(prefix="/v1", tags=["payouts"])


class CreatePayoutRequest(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=128)
    amount: float = Field(gt=0)
    currency: str = Field(default="BRL", max_length=3)
    bank_account: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class PayoutListResponse(BaseModel):
    items: list[PayoutDTO]
    total: int
    page: int
    page_size: int


@router.post("/payouts", response_model=PayoutDTO, status_code=201)
def create(
    req: CreatePayoutRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return create_payout(
        db,
        tenant_id,
        req.recipient_id,
        Decimal(str(req.amount)),
        req.currency,
        bank_account=req.bank_account,
        description=req.description,
    )


@router.get("/payouts", response_model=PayoutListResponse)
def list_(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    items, total = list_payouts(db, tenant_id, status=status, page=page, page_size=page_size)
    return PayoutListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/payouts/{payout_id}", response_model=PayoutDTO)
def get(
    payout_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    return get_payout(db, tenant_id, payout_id)


@router.post("/payouts/{payout_id}/process", response_model=PayoutDTO)
def process(
    payout_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return process_payout(db, tenant_id, payout_id)


@router.post("/payouts/{payout_id}/cancel", response_model=PayoutDTO)
def cancel(
    payout_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return cancel_payout(db, tenant_id, payout_id)
