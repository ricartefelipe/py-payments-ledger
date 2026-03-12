from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.invoicing import (
    InvoiceDTO,
    cancel_invoice,
    create_invoice,
    get_invoice,
    issue_invoice,
    list_invoices,
    mark_invoice_paid,
)

router = APIRouter(prefix="/v1", tags=["invoices"])


class InvoiceItemInput(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, default=1)
    unit_price_cents: int = Field(ge=0)


class BuyerInfo(BaseModel):
    name: str | None = None
    email: str | None = None
    tax_id: str | None = None
    tax_cents: int = Field(ge=0, default=0)


class CreateInvoiceRequest(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    items: list[InvoiceItemInput] = Field(min_length=1)
    buyer: BuyerInfo | None = None
    payment_intent_id: str | None = None
    notes: str | None = None
    due_at: datetime | None = None


class PagedInvoices(BaseModel):
    data: list[InvoiceDTO]
    total: int
    limit: int
    offset: int


class MarkPaidRequest(BaseModel):
    payment_intent_id: str | None = None


@router.post("/invoices", response_model=InvoiceDTO)
def create(
    req: CreateInvoiceRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("invoices:write")),
):
    buyer_dict = None
    if req.buyer:
        buyer_dict = req.buyer.model_dump()
    items_list = [it.model_dump() for it in req.items]
    return create_invoice(
        db,
        tenant_id,
        req.currency,
        items_list,
        buyer_info=buyer_dict,
        payment_intent_id=req.payment_intent_id,
        notes=req.notes,
        due_at=req.due_at,
    )


@router.get("/invoices", response_model=PagedInvoices)
def list_all(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("invoices:read")),
):
    items, total = list_invoices(db, tenant_id, status=status, limit=limit, offset=offset)
    return PagedInvoices(data=items, total=total, limit=limit, offset=offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDTO)
def get_one(
    invoice_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("invoices:read")),
):
    return get_invoice(db, invoice_id, tenant_id)


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceDTO)
def issue(
    invoice_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("invoices:write")),
):
    return issue_invoice(db, invoice_id, tenant_id)


@router.post("/invoices/{invoice_id}/pay", response_model=InvoiceDTO)
def pay(
    invoice_id: str,
    req: MarkPaidRequest | None = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("invoices:write")),
):
    pi_id = req.payment_intent_id if req else None
    return mark_invoice_paid(db, invoice_id, tenant_id, payment_intent_id=pi_id)


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceDTO)
def cancel(
    invoice_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("invoices:write")),
):
    return cancel_invoice(db, invoice_id, tenant_id)
