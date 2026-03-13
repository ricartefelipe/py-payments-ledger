"""Live documentation for IA/LLM agents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.infrastructure.db.models import AuditLog, LedgerEntry, LedgerLine, PaymentIntent
from src.shared.metrics import CIRCUIT_BREAKER_STATE

router = APIRouter(prefix="/v1", tags=["ai"])


def _get_circuit_breaker_state() -> dict[str, int]:
    """Read circuit breaker state from Prometheus gauges."""
    try:
        samples = list(CIRCUIT_BREAKER_STATE.collect())
        state: dict[str, int] = {}
        for family in samples:
            for s in family.samples:
                label = s.labels.get("state", "")
                if label:
                    state[label] = int(s.value)
        return state if state else {"closed": 0, "open": 0}
    except Exception:
        return {"closed": 0, "open": 0}


@router.get("/ai/docs")
def ai_docs(
    request: Request,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("analytics:read")),
):
    """Structured JSON for IA/LLM: API overview, stats, ledger summary, health, recent activity."""
    app = request.app
    settings = app.state.settings

    # API surface overview
    openapi = app.openapi()
    paths = openapi.get("paths", {})
    api_overview: list[dict[str, str]] = []
    for path, methods in paths.items():
        for method, spec in methods.items():
            if method in ("get", "post", "put", "patch", "delete") and isinstance(spec, dict):
                api_overview.append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "summary": spec.get("summary", ""),
                        "tags": ",".join(spec.get("tags", [])),
                    }
                )

    # Payment statistics by status
    payment_stats_q = (
        select(PaymentIntent.status, func.count().label("cnt"))
        .where(PaymentIntent.tenant_id == tenant_id)
        .group_by(PaymentIntent.status)
    )
    payment_stats_rows = db.execute(payment_stats_q).all()
    payment_statistics = {row.status: row.cnt for row in payment_stats_rows}

    # Ledger summary
    entry_count_q = (
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.tenant_id == tenant_id)
    )
    entry_count = db.execute(entry_count_q).scalar_one() or 0
    line_count_q = (
        select(func.count())
        .select_from(LedgerLine)
        .where(LedgerLine.tenant_id == tenant_id)
    )
    line_count = db.execute(line_count_q).scalar_one() or 0
    debit_sum_q = (
        select(func.coalesce(func.sum(LedgerLine.amount), 0))
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(LedgerEntry.tenant_id == tenant_id, LedgerLine.side == "DEBIT")
    )
    credit_sum_q = (
        select(func.coalesce(func.sum(LedgerLine.amount), 0))
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(LedgerEntry.tenant_id == tenant_id, LedgerLine.side == "CREDIT")
    )
    debit_total = float(db.execute(debit_sum_q).scalar_one() or 0)
    credit_total = float(db.execute(credit_sum_q).scalar_one() or 0)
    ledger_summary = {
        "total_entries": entry_count,
        "total_lines": line_count,
        "total_debits": debit_total,
        "total_credits": credit_total,
    }

    # System health and configuration
    health_status = "unknown"
    try:
        from src.infrastructure.db.session import get_engine
        from src.infrastructure.redis.client import get_redis

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        get_redis().ping()
        health_status = "ok"
    except Exception as e:
        health_status = f"degraded: {e!s}"
    config_summary = {
        "app_env": settings.app_env,
        "app_name": settings.app_name,
        "gateway_provider": settings.gateway_provider,
        "orders_integration_enabled": settings.orders_integration_enabled,
        "reconciliation_enabled": settings.reconciliation_enabled,
    }

    # Gateway and circuit breaker
    cb_state = _get_circuit_breaker_state()
    gateway_status = {
        "provider": settings.gateway_provider,
        "circuit_breaker": cb_state,
        "circuit_breaker_threshold": settings.circuit_breaker_failure_threshold,
        "circuit_breaker_recovery_seconds": settings.circuit_breaker_recovery_timeout,
    }

    # Recent activity (last 10 audit entries)
    recent_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    )
    recent_rows = db.execute(recent_q).scalars().all()
    recent_activity = [
        {
            "action": r.action,
            "target": r.target,
            "actor": r.actor_sub,
            "created_at": r.created_at.isoformat(),
        }
        for r in recent_rows
    ]

    return {
        "api_surface": api_overview,
        "payment_statistics": payment_statistics,
        "ledger_summary": ledger_summary,
        "system_health": {"status": health_status},
        "configuration": config_summary,
        "gateway_status": gateway_status,
        "recent_activity": recent_activity,
    }
