from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.disputes import (
    DisputeDTO,
    accept_dispute,
    get_dispute,
    list_disputes,
    open_dispute,
    resolve_dispute,
    submit_evidence,
)

router = APIRouter(prefix="/v1", tags=["disputes"])


class OpenDisputeRequest(BaseModel):
    payment_intent_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=64)
    amount: float | None = Field(default=None, gt=0)


class SubmitEvidenceRequest(BaseModel):
    evidence: dict[str, Any]


class ResolveDisputeRequest(BaseModel):
    won: bool


class DisputeListResponse(BaseModel):
    items: list[DisputeDTO]
    total: int
    page: int
    page_size: int


@router.post("/disputes", response_model=DisputeDTO, status_code=201)
def open_(
    req: OpenDisputeRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return open_dispute(
        db,
        tenant_id,
        req.payment_intent_id,
        req.reason,
        amount=Decimal(str(req.amount)) if req.amount is not None else None,
    )


@router.get("/disputes", response_model=DisputeListResponse)
def list_(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    items, total = list_disputes(db, tenant_id, status=status, page=page, page_size=page_size)
    return DisputeListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/disputes/{dispute_id}", response_model=DisputeDTO)
def get(
    dispute_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    return get_dispute(db, tenant_id, dispute_id)


@router.post("/disputes/{dispute_id}/evidence", response_model=DisputeDTO)
def evidence(
    dispute_id: uuid.UUID,
    req: SubmitEvidenceRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return submit_evidence(db, tenant_id, dispute_id, req.evidence)


@router.post("/disputes/{dispute_id}/accept", response_model=DisputeDTO)
def accept(
    dispute_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return accept_dispute(db, tenant_id, dispute_id)


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeDTO)
def resolve(
    dispute_id: uuid.UUID,
    req: ResolveDisputeRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return resolve_dispute(db, tenant_id, dispute_id, req.won)
