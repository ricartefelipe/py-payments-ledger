from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.application.accounts import seed_default_accounts
from src.infrastructure.db.models import Tenant
from src.infrastructure.db.session import safe_begin
from src.shared.logging import get_logger

log = get_logger(__name__)


def _event_type_from_routing_key(routing_key: str) -> str:
    """spring-saas-core uses keys like saas.TENANT.tenant.created (see OutboxPublisher)."""
    parts = routing_key.split(".")
    if len(parts) >= 3 and parts[0] == "saas":
        return ".".join(parts[2:])
    return routing_key


def _tenant_id_and_fields(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Core outbox sends an envelope; integration tests may pass a flat tenant dict."""
    inner = body.get("payload")
    if isinstance(inner, dict) and "eventType" in body:
        agg = body.get("aggregateType")
        agg_id = str(body.get("aggregateId") or "")
        tid = inner.get("tenantId") or inner.get("tenant_id") or (
            agg_id if agg == "TENANT" else None
        )
        return str(tid or ""), inner
    tid = body.get("tenant_id") or body.get("tenantId")
    return str(tid or ""), body


def handle_tenant_event(session: Session, routing_key: str, payload: dict[str, Any]) -> None:
    tenant_id, fields = _tenant_id_and_fields(payload)
    if not tenant_id:
        log.warning("tenant event missing tenant_id", extra={"routing_key": routing_key})
        return

    event_type = _event_type_from_routing_key(routing_key)
    if event_type == "tenant.created":
        _handle_created(session, tenant_id, fields)
    elif event_type == "tenant.updated":
        _handle_updated(session, tenant_id, fields)
    elif event_type == "tenant.deleted":
        _handle_deleted(session, tenant_id)


def _handle_created(session: Session, tenant_id: str, payload: dict[str, Any]) -> None:
    with safe_begin(session):
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
    with safe_begin(session):
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
    with safe_begin(session):
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            return
        tenant.name = f"[DELETED] {tenant.name}"

    log.info("tenant soft-deleted from event", extra={"tenant_id": tenant_id})
