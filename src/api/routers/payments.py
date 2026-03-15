from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.payments import (
    PaymentIntentDTO,
    confirm_payment_intent,
    create_payment_intent,
    get_payment_intent,
    list_payment_intents,
    void_payment_intent,
)
from src.infrastructure.gateway.factory import create_gateway
from src.infrastructure.redis.client import get_redis
from src.infrastructure.redis.idempotency import IdempotencyStore
from src.shared.problem import http_problem

router = APIRouter(prefix="/v1", tags=["payments"])

_gateway_cache: dict = {}


def get_gateway(request: Request):
    settings = request.app.state.settings
    key = id(settings)
    if key not in _gateway_cache:
        _gateway_cache[key] = create_gateway(settings)
    return _gateway_cache[key]


class PagedPaymentIntents(BaseModel):
    data: list[PaymentIntentDTO]
    total: int
    page: int
    pageSize: int


FRONT_LIST_PAYMENT_INTENTS_TTL = 60  # seconds; see CACHE-REDIS-FRONT in fluxe-b2b-suite docs


@router.get("/payment-intents", response_model=PagedPaymentIntents)
def list_all(
    status: str | None = None,
    customer_ref: str | None = None,
    page: int = 1,
    pageSize: int = 25,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    sig = f"{tenant_id}|{status}|{customer_ref}|{page}|{pageSize}"
    query_hash = hashlib.sha256(sig.encode()).hexdigest()[:16]
    cache_key = f"front:cache:payments:v1/payment-intents:{tenant_id}:{query_hash}"

    try:
        redis = get_redis()
        raw = redis.get(cache_key)
        if raw is not None and isinstance(raw, (str, bytes)):
            data = json.loads(raw)
            return PagedPaymentIntents(**data)
    except Exception:
        pass

    items, total = list_payment_intents(
        db, tenant_id, status=status, customer_ref=customer_ref, page=page, page_size=pageSize
    )
    response = PagedPaymentIntents(data=items, total=total, page=page, pageSize=pageSize)
    try:
        get_redis().setex(
            cache_key,
            FRONT_LIST_PAYMENT_INTENTS_TTL,
            response.model_dump_json(),
        )
    except Exception:
        pass
    return response


class CreatePaymentIntentRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=8)
    customer_ref: str = Field(min_length=1, max_length=128)
    gateway: str | None = Field(default=None, max_length=32)
    payment_type: str | None = Field(default=None, max_length=32)
    payment_method_id: str | None = Field(default=None, description="SavedPaymentMethod UUID")


@router.post("/payment-intents", response_model=PaymentIntentDTO)
def create(
    req: CreatePaymentIntentRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
    gateway: object = Depends(get_gateway),
):
    if not idempotency_key:
        raise http_problem(
            400,
            "Bad Request",
            "Missing Idempotency-Key header (required for POST /v1/payment-intents)",
            instance="/v1/payment-intents",
        )
    ttl = request.app.state.settings.idempotency_ttl_seconds
    store = IdempotencyStore(get_redis(), ttl_seconds=ttl)
    idem_key = f"idem:{tenant_id}:create:{idempotency_key}"
    hit = store.get(idem_key)
    if hit.hit and hit.value:
        return PaymentIntentDTO(**hit.value)

    dto = create_payment_intent(
        db,
        tenant_id,
        req.amount,
        req.currency,
        req.customer_ref,
        gateway=gateway,
        idempotency_key=idempotency_key,
        gateway_provider=req.gateway,
        payment_type=req.payment_type,
        settings=request.app.state.settings,
        payment_method_id=req.payment_method_id,
    )
    store.set(idem_key, dto.model_dump())
    return dto


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
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
    gateway: object = Depends(get_gateway),
):
    if not idempotency_key:
        raise http_problem(
            400,
            "Bad Request",
            "Missing Idempotency-Key",
            instance=f"/v1/payment-intents/{pid}/confirm",
        )
    ttl = request.app.state.settings.idempotency_ttl_seconds
    store = IdempotencyStore(get_redis(), ttl_seconds=ttl)
    idem_key = f"idem:{tenant_id}:confirm:{pid}:{idempotency_key}"
    hit = store.get(idem_key)
    if hit.hit and hit.value:
        return PaymentIntentDTO(**hit.value)

    dto = confirm_payment_intent(
        db,
        tenant_id,
        pid,
        gateway=gateway,
        idempotency_key=idempotency_key,
    )
    store.set(idem_key, dto.model_dump())
    return dto


@router.post("/payment-intents/{pid}/void", response_model=PaymentIntentDTO)
def void(
    pid: uuid.UUID,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
    gateway: object = Depends(get_gateway),
):
    if not idempotency_key:
        raise http_problem(
            400,
            "Bad Request",
            "Missing Idempotency-Key",
            instance=f"/v1/payment-intents/{pid}/void",
        )
    ttl = request.app.state.settings.idempotency_ttl_seconds
    store = IdempotencyStore(get_redis(), ttl_seconds=ttl)
    idem_key = f"idem:{tenant_id}:void:{pid}:{idempotency_key}"
    hit = store.get(idem_key)
    if hit.hit and hit.value:
        return PaymentIntentDTO(**hit.value)

    dto = void_payment_intent(
        db,
        tenant_id,
        pid,
        gateway=gateway,
        idempotency_key=idempotency_key,
    )
    store.set(idem_key, dto.model_dump())
    return dto
