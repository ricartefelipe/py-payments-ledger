from __future__ import annotations

import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.infrastructure.redis.client import get_redis

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class ChaosConfig(BaseModel):
    enabled: bool = False
    fail_percent: int = Field(ge=0, le=100, default=0)
    latency_ms: int = Field(ge=0, le=30_000, default=0)


@router.get("/chaos", response_model=ChaosConfig)
def get_chaos(
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    r = get_redis()
    raw = r.get(f"chaos:{tenant_id}")
    if not raw:
        return ChaosConfig()
    return ChaosConfig(**json.loads(raw))


@router.put("/chaos", response_model=ChaosConfig)
def set_chaos(
    cfg: ChaosConfig,
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    r = get_redis()
    r.set(f"chaos:{tenant_id}", cfg.model_dump_json())
    return cfg
