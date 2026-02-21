from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.payments import (
    PaymentIntentDTO,
    confirm_payment_intent,
    create_payment_intent,
    get_payment_intent,
)
from src.infrastructure.redis.client import get_redis
from src.infrastructure.redis.idempotency import IdempotencyStore
from src.shared.problem import http_problem

router = APIRouter(prefix="/v1", tags=["payments"])


class CreatePaymentIntentRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=8)
    customer_ref: str = Field(min_length=1, max_length=128)


@router.post("/payment-intents", response_model=PaymentIntentDTO)
def create(
    req: CreatePaymentIntentRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return create_payment_intent(db, tenant_id, req.amount, req.currency, req.customer_ref)


@router.get("/payment-intents/{pid}", response_model=PaymentIntentDTO)
def get_one(
    pid: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    return get_payment_intent(db, tenant_id, pid)


@router.post("/payment-intents/{pid}/confirm", response_model=PaymentIntentDTO)
def confirm(
    pid: uuid.UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    if not idempotency_key:
        raise http_problem(
            400,
            "Bad Request",
            "Missing Idempotency-Key",
            instance=f"/v1/payment-intents/{pid}/confirm",
        )
    store = IdempotencyStore(get_redis())
    idem_key = f"idem:{tenant_id}:confirm:{pid}:{idempotency_key}"
    hit = store.get(idem_key)
    if hit.hit and hit.value:
        return PaymentIntentDTO(**hit.value)

    dto = confirm_payment_intent(db, tenant_id, pid)
    store.set(idem_key, dto.model_dump())
    return dto
