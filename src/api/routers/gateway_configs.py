from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.security import _audit
from src.infrastructure.db.models import GatewayConfig
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

router = APIRouter(prefix="/v1", tags=["gateway-configs"])

log = get_logger(__name__)


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class GatewayConfigDTO(BaseModel):
    id: str
    tenant_id: str
    provider: str
    api_key_ref: str | None
    is_default: bool
    supported_currencies: list[str]
    payment_types: list[str]


class CreateGatewayConfigRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    api_key_ref: str | None = Field(default=None, max_length=128)
    is_default: bool = False
    supported_currencies: list[str] = Field(default_factory=list, max_length=20)
    payment_types: list[str] = Field(default_factory=list, max_length=20)


@router.get("/gateway-configs", response_model=list[GatewayConfigDTO])
def list_configs(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    rows = (
        db.execute(select(GatewayConfig).where(GatewayConfig.tenant_id == tenant_id))
        .scalars()
        .all()
    )
    return [
        GatewayConfigDTO(
            id=str(r.id),
            tenant_id=r.tenant_id,
            provider=r.provider,
            api_key_ref=r.api_key_ref,
            is_default=r.is_default,
            supported_currencies=list(r.supported_currencies or []),
            payment_types=list(r.payment_types or []),
        )
        for r in rows
    ]


@router.post("/gateway-configs", response_model=GatewayConfigDTO, status_code=201)
def create_config(
    req: CreateGatewayConfigRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    with safe_begin(db):
        existing = db.execute(
            select(GatewayConfig).where(
                GatewayConfig.tenant_id == tenant_id,
                GatewayConfig.provider == req.provider,
            )
        ).scalar_one_or_none()
        if existing:
            raise http_problem(
                409,
                "Conflict",
                f"Gateway config for provider {req.provider} already exists",
                instance="/v1/gateway-configs",
            )
        if req.is_default:
            for c in (
                db.execute(select(GatewayConfig).where(GatewayConfig.tenant_id == tenant_id))
                .scalars()
                .all()
            ):
                c.is_default = False
        now = _utcnow()
        cfg = GatewayConfig(
            tenant_id=tenant_id,
            provider=req.provider,
            api_key_ref=req.api_key_ref,
            is_default=req.is_default,
            supported_currencies=req.supported_currencies or [],
            payment_types=req.payment_types or [],
            created_at=now,
            updated_at=now,
        )
        db.add(cfg)
        db.flush()

    _audit(
        db,
        tenant_id,
        get_subject() or "system",
        "gateway_config.created",
        f"gateway_config:{cfg.id}",
        {"provider": req.provider, "is_default": req.is_default},
    )

    return GatewayConfigDTO(
        id=str(cfg.id),
        tenant_id=cfg.tenant_id,
        provider=cfg.provider,
        api_key_ref=cfg.api_key_ref,
        is_default=cfg.is_default,
        supported_currencies=list(cfg.supported_currencies or []),
        payment_types=list(cfg.payment_types or []),
    )


@router.delete("/gateway-configs/{config_id}", status_code=204)
def delete_config(
    config_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    with safe_begin(db):
        cfg = db.execute(
            select(GatewayConfig).where(
                GatewayConfig.id == config_id,
                GatewayConfig.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if not cfg:
            raise http_problem(
                404,
                "Not Found",
                "gateway config not found",
                instance=f"/v1/gateway-configs/{config_id}",
            )
        db.delete(cfg)

    _audit(
        db,
        tenant_id,
        get_subject() or "system",
        "gateway_config.deleted",
        f"gateway_config:{config_id}",
        {"provider": cfg.provider},
    )
