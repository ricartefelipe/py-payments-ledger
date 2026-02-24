from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.reconciliation import (
    DiscrepancyDTO,
    list_discrepancies,
    resolve_discrepancy,
)

router = APIRouter(prefix="/v1", tags=["reconciliation"])


@router.get("/reconciliation/discrepancies", response_model=list[DiscrepancyDTO])
def list_(
    resolved: Optional[bool] = Query(default=None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return list_discrepancies(db, tenant_id, resolved)


@router.post("/reconciliation/discrepancies/{disc_id}/resolve", response_model=DiscrepancyDTO)
def resolve(
    disc_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return resolve_discrepancy(db, tenant_id, disc_id)
