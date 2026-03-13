from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.recurring import (
    RecurringChargeDTO,
    cancel_recurring_charge,
    create_recurring_charge,
    list_recurring_charges,
    pause_recurring_charge,
    resume_recurring_charge,
)

router = APIRouter(prefix="/v1", tags=["recurring"])


class CreateRecurringChargeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    interval: str = Field(pattern=r"^(monthly|yearly)$")
    gateway_customer_ref: str | None = None


@router.post("/recurring-charges", response_model=RecurringChargeDTO)
def create(
    req: CreateRecurringChargeRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return create_recurring_charge(
        db,
        tenant_id,
        req.description,
        req.amount_cents,
        req.currency,
        req.interval,
        gateway_customer_ref=req.gateway_customer_ref,
    )


@router.get("/recurring-charges", response_model=list[RecurringChargeDTO])
def list_all(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    return list_recurring_charges(db, tenant_id)


@router.post("/recurring-charges/{charge_id}/pause", response_model=RecurringChargeDTO)
def pause(
    charge_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return pause_recurring_charge(db, charge_id, tenant_id)


@router.post("/recurring-charges/{charge_id}/resume", response_model=RecurringChargeDTO)
def resume(
    charge_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return resume_recurring_charge(db, charge_id, tenant_id)


@router.post("/recurring-charges/{charge_id}/cancel", response_model=RecurringChargeDTO)
def cancel(
    charge_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return cancel_recurring_charge(db, charge_id, tenant_id)
