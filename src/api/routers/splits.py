from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.splits import (
    PaymentSplitDTO,
    SplitInput,
    create_split,
    list_splits,
    process_splits,
)

router = APIRouter(prefix="/v1", tags=["splits"])


class CreateSplitsRequest(BaseModel):
    splits: list[SplitInput] = Field(min_length=1)


@router.post(
    "/payment-intents/{pid}/splits",
    response_model=list[PaymentSplitDTO],
)
def create(
    pid: uuid.UUID,
    req: CreateSplitsRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return create_split(db, tenant_id, pid, req.splits)


@router.get(
    "/payment-intents/{pid}/splits",
    response_model=list[PaymentSplitDTO],
)
def list_(
    pid: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:read")),
):
    return list_splits(db, tenant_id, pid)


@router.post(
    "/payment-intents/{pid}/splits/process",
    response_model=list[PaymentSplitDTO],
)
def process(
    pid: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("payments:write")),
):
    return process_splits(db, tenant_id, pid)
