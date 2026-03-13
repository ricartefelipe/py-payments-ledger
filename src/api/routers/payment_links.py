from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.payment_links import (
    PaymentLinkDTO,
    cancel_payment_link,
    create_payment_link,
    get_payment_link,
    list_payment_links,
    use_payment_link,
)

router = APIRouter(prefix="/v1", tags=["payment-links"])


class CreatePaymentLinkRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, default="BRL")
    description: str | None = Field(default=None, max_length=500)
    customer_email: str | None = Field(default=None, max_length=320)
    expires_hours: int = Field(default=24, ge=1, le=8760)
    metadata: dict[str, Any] | None = None


class PagedPaymentLinks(BaseModel):
    data: list[PaymentLinkDTO]
    total: int
    page: int
    pageSize: int


class PayRequest(BaseModel):
    customer_ref: str = Field(min_length=1, max_length=256)


@router.post("/payment-links", response_model=PaymentLinkDTO)
def create(
    req: CreatePaymentLinkRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return create_payment_link(
        db,
        tenant_id,
        req.amount,
        req.currency,
        description=req.description,
        customer_email=req.customer_email,
        expires_hours=req.expires_hours,
        metadata=req.metadata,
    )


@router.get("/payment-links", response_model=PagedPaymentLinks)
def list_all(
    status: str | None = None,
    page: int = 1,
    pageSize: int = 25,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    items, total = list_payment_links(db, tenant_id, status=status, page=page, page_size=pageSize)
    return PagedPaymentLinks(data=items, total=total, page=page, pageSize=pageSize)


@router.get("/payment-links/{link_id}", response_model=PaymentLinkDTO)
def get_one(
    link_id: str,
    db: Session = Depends(get_db),
):
    return get_payment_link(db, link_id)


@router.post("/payment-links/{link_id}/pay", response_model=PaymentLinkDTO)
def pay(
    link_id: str,
    req: PayRequest,
    db: Session = Depends(get_db),
):
    return use_payment_link(db, link_id, req.customer_ref)


@router.post("/payment-links/{link_id}/cancel", response_model=PaymentLinkDTO)
def cancel(
    link_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return cancel_payment_link(db, tenant_id, link_id)
