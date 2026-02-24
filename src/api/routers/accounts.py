from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.accounts import AccountConfigDTO, create_account, list_accounts

router = APIRouter(prefix="/v1", tags=["accounts"])


class CreateAccountRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    account_type: str = Field(min_length=1, max_length=32)


@router.post("/accounts", response_model=AccountConfigDTO, status_code=201)
def create(
    req: CreateAccountRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return create_account(db, tenant_id, req.code, req.label, req.account_type)


@router.get("/accounts", response_model=list[AccountConfigDTO])
def list_(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("ledger:read")),
):
    return list_accounts(db, tenant_id)
