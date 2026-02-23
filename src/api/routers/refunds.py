from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.refunds import RefundDTO, create_refund, list_refunds
from src.infrastructure.redis.client import get_redis
from src.infrastructure.redis.idempotency import IdempotencyStore
from src.shared.problem import http_problem

router = APIRouter(prefix="/v1", tags=["refunds"])


class CreateRefundRequest(BaseModel):
    amount: float = Field(gt=0)
    reason: str | None = Field(default=None, max_length=500)


@router.post("/payment-intents/{pid}/refund", response_model=RefundDTO)
def refund(
    pid: uuid.UUID,
    req: CreateRefundRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    if not idempotency_key:
        raise http_problem(
            400, "Bad Request", "Missing Idempotency-Key",
            instance=f"/v1/payment-intents/{pid}/refund",
        )
    ttl = request.app.state.settings.idempotency_ttl_seconds
    store = IdempotencyStore(get_redis(), ttl_seconds=ttl)
    idem_key = f"idem:{tenant_id}:refund:{pid}:{idempotency_key}"
    hit = store.get(idem_key)
    if hit.hit and hit.value:
        return RefundDTO(**hit.value)

    dto = create_refund(db, tenant_id, pid, Decimal(str(req.amount)), req.reason)
    store.set(idem_key, dto.model_dump())
    return dto


@router.get("/payment-intents/{pid}/refunds", response_model=list[RefundDTO])
def list_(
    pid: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    return list_refunds(db, tenant_id, pid)
