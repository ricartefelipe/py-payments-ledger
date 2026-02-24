from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.accounts import seed_default_accounts
from src.infrastructure.db.models import Tenant
from src.shared.logging import get_logger

log = get_logger(__name__)


def handle_tenant_event(session: Session, routing_key: str, payload: dict[str, Any]) -> None:
    tenant_id = str(payload.get("tenant_id") or payload.get("tenantId") or "")
    if not tenant_id:
        log.warning("tenant event missing tenant_id", extra={"routing_key": routing_key})
        return

    if routing_key == "tenant.created":
        _handle_created(session, tenant_id, payload)
    elif routing_key == "tenant.updated":
        _handle_updated(session, tenant_id, payload)
    elif routing_key == "tenant.deleted":
        _handle_deleted(session, tenant_id)


def _handle_created(session: Session, tenant_id: str, payload: dict[str, Any]) -> None:
    with session.begin():
        existing = session.get(Tenant, tenant_id)
        if existing:
            log.info("tenant already exists, skipping", extra={"tenant_id": tenant_id})
            return

        name = str(payload.get("name") or payload.get("tenantName") or tenant_id)
        plan = str(payload.get("plan") or "pro")
        region = str(payload.get("region") or "region-a")

        session.add(Tenant(id=tenant_id, name=name, plan=plan, region=region))
        session.flush()
        seed_default_accounts(session, tenant_id)

    log.info("tenant created from event", extra={"tenant_id": tenant_id})


def _handle_updated(session: Session, tenant_id: str, payload: dict[str, Any]) -> None:
    with session.begin():
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            log.warning("tenant not found for update", extra={"tenant_id": tenant_id})
            return

        if "name" in payload or "tenantName" in payload:
            tenant.name = str(payload.get("name") or payload.get("tenantName"))
        if "plan" in payload:
            tenant.plan = str(payload["plan"])
        if "region" in payload:
            tenant.region = str(payload["region"])

    log.info("tenant updated from event", extra={"tenant_id": tenant_id})


def _handle_deleted(session: Session, tenant_id: str) -> None:
    with session.begin():
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            return
        tenant.name = f"[DELETED] {tenant.name}"

    log.info("tenant soft-deleted from event", extra={"tenant_id": tenant_id})
