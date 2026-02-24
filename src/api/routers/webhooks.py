from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.webhooks import (
    WebhookEndpointDTO,
    create_webhook_endpoint,
    delete_webhook_endpoint,
    list_webhook_endpoints,
)

router = APIRouter(prefix="/v1", tags=["webhooks"])


class CreateWebhookRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    events: list[str] = Field(min_length=1)


@router.post("/webhooks", response_model=WebhookEndpointDTO, status_code=201)
def create(
    req: CreateWebhookRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return create_webhook_endpoint(db, tenant_id, req.url, req.events)


@router.get("/webhooks", response_model=list[WebhookEndpointDTO])
def list_(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    return list_webhook_endpoints(db, tenant_id)


@router.delete("/webhooks/{endpoint_id}", status_code=204)
def delete(
    endpoint_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("admin:write")),
):
    delete_webhook_endpoint(db, tenant_id, endpoint_id)
