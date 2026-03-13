from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.infrastructure.db.models import SavedPaymentMethod
from src.infrastructure.gateway.factory import get_gateway_for_tenant
from src.shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/v1/payment-methods", tags=["Payment Methods"])


class SavePaymentMethodRequest(BaseModel):
    payment_token: str = Field(..., description="Single-use token from frontend SDK")
    gateway_provider: str = Field(..., description="stripe, pagseguro, or mercadopago")
    customer_ref: str = Field(..., description="Customer identifier")
    label: Optional[str] = Field(None, description="Friendly label (e.g. 'Visa ending 4242')")
    is_default: bool = Field(False)


class PaymentMethodResponse(BaseModel):
    id: str
    customer_ref: str
    gateway_provider: str
    card_last4: Optional[str]
    card_brand: Optional[str]
    card_exp_month: Optional[int]
    card_exp_year: Optional[int]
    label: Optional[str]
    is_default: bool
    is_active: bool
    created_at: str


@router.post("", status_code=201)
async def save_payment_method(
    body: SavePaymentMethodRequest,
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    settings = request.app.state.settings

    gateway = get_gateway_for_tenant(db, tenant_id, settings, provider=body.gateway_provider)

    result = await gateway.save_payment_method(body.customer_ref, body.payment_token)
    if not result.success:
        raise HTTPException(
            status_code=502,
            detail=result.error_message or "Failed to save payment method at gateway",
        )

    if body.is_default:
        existing = (
            db.execute(
                select(SavedPaymentMethod).where(
                    SavedPaymentMethod.tenant_id == tenant_id,
                    SavedPaymentMethod.customer_ref == body.customer_ref,
                    SavedPaymentMethod.is_default.is_(True),
                )
            )
            .scalars()
            .all()
        )
        for pm in existing:
            pm.is_default = False

    spm = SavedPaymentMethod(
        tenant_id=tenant_id,
        customer_ref=body.customer_ref,
        gateway_provider=body.gateway_provider,
        gateway_token=result.gateway_token,
        card_last4=result.card_last4 or None,
        card_brand=result.card_brand or None,
        card_exp_month=result.card_exp_month or None,
        card_exp_year=result.card_exp_year or None,
        label=body.label,
        is_default=body.is_default,
    )
    db.add(spm)
    db.commit()
    db.refresh(spm)

    return _to_response(spm)


@router.get("")
async def list_payment_methods(
    customer_ref: Optional[str] = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    q = select(SavedPaymentMethod).where(
        SavedPaymentMethod.tenant_id == tenant_id,
        SavedPaymentMethod.is_active.is_(True),
    )
    if customer_ref:
        q = q.where(SavedPaymentMethod.customer_ref == customer_ref)
    q = q.order_by(SavedPaymentMethod.created_at.desc())

    methods = db.execute(q).scalars().all()
    return [_to_response(m) for m in methods]


@router.get("/{method_id}")
async def get_payment_method(
    method_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    spm = db.execute(
        select(SavedPaymentMethod).where(
            SavedPaymentMethod.id == uuid.UUID(method_id),
            SavedPaymentMethod.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if not spm:
        raise HTTPException(status_code=404, detail="Payment method not found")
    return _to_response(spm)


@router.delete("/{method_id}", status_code=204)
async def delete_payment_method(
    method_id: str,
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    spm = db.execute(
        select(SavedPaymentMethod).where(
            SavedPaymentMethod.id == uuid.UUID(method_id),
            SavedPaymentMethod.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if not spm:
        raise HTTPException(status_code=404, detail="Payment method not found")

    settings = request.app.state.settings
    gateway = get_gateway_for_tenant(db, tenant_id, settings, provider=spm.gateway_provider)
    await gateway.delete_payment_method(spm.gateway_token)

    spm.is_active = False
    db.commit()


def _to_response(spm: SavedPaymentMethod) -> PaymentMethodResponse:
    return PaymentMethodResponse(
        id=str(spm.id),
        customer_ref=spm.customer_ref,
        gateway_provider=spm.gateway_provider,
        card_last4=spm.card_last4,
        card_brand=spm.card_brand,
        card_exp_month=spm.card_exp_month,
        card_exp_year=spm.card_exp_year,
        label=spm.label,
        is_default=spm.is_default,
        is_active=spm.is_active,
        created_at=spm.created_at.isoformat(),
    )
