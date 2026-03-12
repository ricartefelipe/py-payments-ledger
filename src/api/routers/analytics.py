"""Analytics API for IA/LLM: fraud, ledger anomalies, cashflow forecast."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps.auth import enforce_tenant, require_permission
from src.api.deps.db import get_db
from src.application.analytics import (
    get_cashflow_forecast,
    get_fraud_analytics,
    get_ledger_anomalies,
)

router = APIRouter(prefix="/v1", tags=["analytics"])


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


@router.get("/analytics/fraud")
def fraud_analytics(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("analytics:read")),
):
    """Aggregated data for fraud analysis.

    Returns: payment failure rate by tenant/period, unusual patterns,
    top failed reasons, risk score per tenant.
    """
    from_dt = _parse_dt(from_)
    to_dt = _parse_dt(to)
    return get_fraud_analytics(db, tenant_id, from_dt, to_dt)


@router.get("/analytics/ledger-anomalies")
def ledger_anomalies(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("analytics:read")),
):
    """Ledger anomalies: imbalances, unusual amounts, missing settlements, discrepancy trends."""
    return get_ledger_anomalies(db, tenant_id)


@router.get("/analytics/cashflow-forecast")
def cashflow_forecast(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(enforce_tenant),
    _: object = Depends(require_permission("analytics:read")),
):
    """Cashflow forecast: position, pending auths, historical revenue, projections."""
    return get_cashflow_forecast(db, tenant_id)
