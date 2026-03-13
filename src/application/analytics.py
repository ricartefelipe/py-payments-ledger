"""Analytics queries for IA/LLM features: fraud, ledger anomalies, cashflow forecast."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import (
    LedgerEntry,
    LedgerLine,
    OutboxEvent,
    PaymentIntent,
    ReconciliationDiscrepancy,
    Refund,
    RecurringCharge,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_fraud_analytics(
    session: Session,
    tenant_id: str,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> dict[str, Any]:
    """Aggregated data for fraud analysis: failure rates, patterns, risk score."""

    q = select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id)
    if from_dt:
        q = q.where(PaymentIntent.created_at >= from_dt)
    if to_dt:
        q = q.where(PaymentIntent.created_at <= to_dt)
    payments = list(session.execute(q).scalars().all())

    total = len(payments)
    failed = sum(1 for p in payments if p.status == "FAILED")
    success_statuses = ("SETTLED", "AUTHORIZED", "VOIDED", "CREATED")
    successful = sum(1 for p in payments if p.status in success_statuses)

    failure_rate = (failed / total * 100) if total > 0 else 0.0

    # Failure rate by period (day)
    period_q = (
        select(
            func.date_trunc("day", PaymentIntent.created_at).label("period"),
            PaymentIntent.status,
            func.count().label("cnt"),
        )
        .where(PaymentIntent.tenant_id == tenant_id)
        .group_by(func.date_trunc("day", PaymentIntent.created_at), PaymentIntent.status)
    )
    if from_dt:
        period_q = period_q.where(PaymentIntent.created_at >= from_dt)
    if to_dt:
        period_q = period_q.where(PaymentIntent.created_at <= to_dt)
    period_rows = session.execute(period_q).all()

    failure_by_period: list[dict[str, Any]] = []
    period_totals: dict[str, tuple[int, int]] = {}
    for row in period_rows:
        key = row.period.isoformat() if row.period else ""
        if key not in period_totals:
            period_totals[key] = (0, 0)
        tot, fail = period_totals[key]
        tot += row.cnt
        if row.status == "FAILED":
            fail += row.cnt
        period_totals[key] = (tot, fail)
    for k, (tot, fail) in period_totals.items():
        failure_by_period.append(
            {
                "period": k,
                "total": tot,
                "failed": fail,
                "failure_rate_pct": round((fail / tot * 100), 2) if tot > 0 else 0,
            }
        )
    failure_by_period.sort(key=lambda x: x["period"])

    # High-value payments (> 95th percentile)
    amounts = [float(p.amount) for p in payments if p.amount and p.amount > 0]
    high_value_threshold = 0.0
    if amounts:
        amounts_sorted = sorted(amounts)
        idx = int(len(amounts_sorted) * 0.95) - 1
        high_value_threshold = amounts_sorted[max(0, idx)] if idx >= 0 else amounts_sorted[0]
    high_value_count = sum(
        1 for p in payments if float(p.amount) >= high_value_threshold and high_value_threshold > 0
    )

    # Rapid successive: payments created within 5 min of each other
    created_times = sorted([p.created_at for p in payments])
    rapid_count = 0
    for i in range(1, len(created_times)):
        delta = (created_times[i] - created_times[i - 1]).total_seconds()
        if 0 < delta < 300:  # 5 min
            rapid_count += 1

    # Top failed reasons from OutboxEvent payment.retry_exhausted
    outbox_q = select(OutboxEvent.payload).where(
        OutboxEvent.tenant_id == tenant_id,
        OutboxEvent.event_type == "payment.retry_exhausted",
    )
    if from_dt:
        outbox_q = outbox_q.where(OutboxEvent.created_at >= from_dt)
    if to_dt:
        outbox_q = outbox_q.where(OutboxEvent.created_at <= to_dt)
    outbox_rows = session.execute(outbox_q).scalars().all()
    reason_counts: dict[tuple[str, str], int] = {}
    for payload in outbox_rows:
        if isinstance(payload, dict):
            ec = str(payload.get("error_code") or "unknown")
            em = str(payload.get("error_message") or "")
            key = (ec, em)
            reason_counts[key] = reason_counts.get(key, 0) + 1
    top_failed_reasons = [
        {"error_code": k[0], "error_message": k[1], "count": v}
        for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])[:10]
    ]

    if not top_failed_reasons and failed > 0:
        top_failed_reasons = [
            {
                "error_code": "status_failed",
                "error_message": "Payment status FAILED",
                "count": failed,
            }
        ]

    # Risk score: 0-100 based on failure/success ratio (higher = riskier)
    risk_score = 0
    if total > 0:
        risk_score = min(100, int((failed / total) * 100 * 1.5))  # Scale for visibility

    return {
        "tenant_id": tenant_id,
        "period": {
            "from": (from_dt.isoformat() if from_dt else None),
            "to": (to_dt.isoformat() if to_dt else None),
        },
        "failure_rate_by_tenant_pct": round(failure_rate, 2),
        "failure_by_period": failure_by_period,
        "unusual_patterns": {
            "high_value_payments_count": high_value_count,
            "high_value_threshold": high_value_threshold,
            "rapid_successive_payments_count": rapid_count,
            "geographic_anomalies": [],  # No geo data in PaymentIntent
        },
        "top_failed_reasons": top_failed_reasons,
        "risk_score": risk_score,
        "total_payments": total,
        "failed_count": failed,
        "successful_count": successful,
    }


def get_ledger_anomalies(
    session: Session,
    tenant_id: str,
) -> dict[str, Any]:
    """Ledger anomalies: imbalances, unusual amounts, missing settlements."""

    # 1. Ledger imbalances: entries where sum(debits) != sum(credits) per entry
    subq = (
        select(
            LedgerEntry.id,
            LedgerEntry.tenant_id,
            func.sum(case((LedgerLine.side == "DEBIT", LedgerLine.amount), else_=Decimal(0))).label(
                "debits"
            ),
            func.sum(
                case((LedgerLine.side == "CREDIT", LedgerLine.amount), else_=Decimal(0))
            ).label("credits"),
        )
        .join(LedgerLine, LedgerLine.entry_id == LedgerEntry.id)
        .where(LedgerEntry.tenant_id == tenant_id)
        .group_by(LedgerEntry.id, LedgerEntry.tenant_id)
    ).subquery()
    imbalance_full = select(subq).where(subq.c.debits != subq.c.credits)
    imbalance_rows = session.execute(imbalance_full).all()
    imbalances = [
        {
            "entry_id": str(r.id),
            "debits": str(r.debits),
            "credits": str(r.credits),
            "diff": str(abs(float(r.debits or 0) - float(r.credits or 0))),
        }
        for r in imbalance_rows
    ]

    # 2. Unusual amounts (> 3 std dev)
    amount_stats = (
        select(
            func.avg(LedgerLine.amount).label("avg_amt"),
            func.stddev(LedgerLine.amount).label("std_amt"),
        )
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(LedgerEntry.tenant_id == tenant_id)
    )
    stats_row = session.execute(amount_stats).first()
    avg_amt = float(stats_row.avg_amt or 0) if stats_row is not None else 0.0
    std_amt = float(stats_row.std_amt or 0) if stats_row is not None else 0.0
    threshold = avg_amt + (3 * std_amt) if std_amt and std_amt > 0 else 0
    unusual_q = (
        select(LedgerLine, LedgerEntry)
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(
            LedgerEntry.tenant_id == tenant_id,
            LedgerLine.amount > threshold,
        )
    )
    if threshold > 0:
        unusual_q = unusual_q.limit(50)
    unusual_rows = session.execute(unusual_q).all()
    unusual_amounts = [
        {
            "entry_id": str(r[1].id),
            "line_id": str(r[0].id),
            "amount": str(r[0].amount),
            "account": r[0].account,
            "threshold": threshold,
        }
        for r in unusual_rows
    ]

    # 3. Missing settlement: AUTHORIZED but not SETTLED after 24h
    cutoff = _utcnow() - timedelta(hours=24)
    missing_q = select(PaymentIntent).where(
        PaymentIntent.tenant_id == tenant_id,
        PaymentIntent.status == "AUTHORIZED",
        PaymentIntent.updated_at < cutoff,
    )
    missing_rows = session.execute(missing_q).scalars().all()
    missing_settlements = [
        {
            "payment_intent_id": str(p.id),
            "amount": str(p.amount),
            "currency": p.currency,
            "authorized_at": p.updated_at.isoformat(),
        }
        for p in missing_rows
    ]

    # 4. Reconciliation discrepancy trends (last 30 days by day)
    trend_cutoff = _utcnow() - timedelta(days=30)
    trend_q = (
        select(
            func.date_trunc("day", ReconciliationDiscrepancy.created_at).label("day"),
            func.count().label("cnt"),
        )
        .where(
            ReconciliationDiscrepancy.tenant_id == tenant_id,
            ReconciliationDiscrepancy.created_at >= trend_cutoff,
        )
        .group_by(func.date_trunc("day", ReconciliationDiscrepancy.created_at))
        .order_by(func.date_trunc("day", ReconciliationDiscrepancy.created_at))
    )
    trend_rows = session.execute(trend_q).all()
    discrepancy_trends = [
        {"day": r.day.isoformat() if r.day else "", "count": r[1]} for r in trend_rows
    ]

    return {
        "tenant_id": tenant_id,
        "ledger_imbalances": imbalances,
        "unusual_transaction_amounts": unusual_amounts,
        "missing_settlement_entries": missing_settlements,
        "reconciliation_discrepancy_trends": discrepancy_trends,
    }


def get_cashflow_forecast(
    session: Session,
    tenant_id: str,
) -> dict[str, Any]:
    """Cashflow forecast: position, pending auths, historical revenue, projections."""

    # Current cash position: total settled - total refunded
    settled_rev = (
        select(func.coalesce(func.sum(LedgerLine.amount), 0))
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(
            LedgerEntry.tenant_id == tenant_id,
            LedgerLine.account == "REVENUE",
            LedgerLine.side == "CREDIT",
        )
    )
    settled_row = session.execute(settled_rev).scalar_one()
    total_settled = float(settled_row or 0)

    refund_sum = select(func.coalesce(func.sum(Refund.amount), 0)).where(
        Refund.tenant_id == tenant_id, Refund.status == "COMPLETED"
    )
    refund_row = session.execute(refund_sum).scalar_one()
    total_refunded = float(refund_row or 0)
    cash_position = total_settled - total_refunded

    # Pending authorizations (expected future settlements)
    pending_auth_q = select(func.coalesce(func.sum(PaymentIntent.amount), 0)).where(
        PaymentIntent.tenant_id == tenant_id,
        PaymentIntent.status == "AUTHORIZED",
    )
    pending_row = session.execute(pending_auth_q).scalar_one()
    pending_authorizations = float(pending_row or 0)

    # Historical daily revenue (last 30 days)
    hist_cutoff = _utcnow() - timedelta(days=30)
    hist_q = (
        select(
            func.date_trunc("day", LedgerEntry.posted_at).label("day"),
            LedgerLine.currency,
            func.coalesce(func.sum(LedgerLine.amount), 0).label("total"),
        )
        .join(LedgerEntry, LedgerLine.entry_id == LedgerEntry.id)
        .where(
            LedgerEntry.tenant_id == tenant_id,
            LedgerLine.account == "REVENUE",
            LedgerLine.side == "CREDIT",
            LedgerEntry.posted_at >= hist_cutoff,
        )
        .group_by(func.date_trunc("day", LedgerEntry.posted_at), LedgerLine.currency)
        .order_by(func.date_trunc("day", LedgerEntry.posted_at))
    )
    hist_rows = session.execute(hist_q).all()
    historical_daily_revenue = [
        {"day": r.day.isoformat() if r.day else "", "currency": r.currency, "total": str(r.total)}
        for r in hist_rows
    ]

    # Projected revenue (next 7 days) - simple trend: daily avg * 7
    daily_totals: dict[str, float] = {}
    for h in historical_daily_revenue:
        k = h["day"][:10]
        daily_totals[k] = daily_totals.get(k, 0) + float(h["total"])
    avg_daily = sum(daily_totals.values()) / len(daily_totals) if daily_totals else 0
    projected_next_7_days = avg_daily * 7

    # Recurring charges expected revenue (next 30 days)
    recur_cutoff = _utcnow() + timedelta(days=30)
    recur_q = (
        select(
            func.sum(RecurringCharge.amount_cents / 100.0).label("total"),
            RecurringCharge.currency,
        )
        .where(
            RecurringCharge.tenant_id == tenant_id,
            RecurringCharge.status == "ACTIVE",
            RecurringCharge.next_charge_at <= recur_cutoff,
        )
        .group_by(RecurringCharge.currency)
    )
    recur_rows = session.execute(recur_q).all()
    recurring_expected = [
        {
            "currency": r.currency,
            "total_cents": int(r.total * 100) if r.total else 0,
            "total": str(r.total or 0),
        }
        for r in recur_rows
    ]

    return {
        "tenant_id": tenant_id,
        "current_cash_position": cash_position,
        "total_settled": total_settled,
        "total_refunded": total_refunded,
        "pending_authorizations": pending_authorizations,
        "historical_daily_revenue": historical_daily_revenue,
        "projected_revenue_next_7_days": round(projected_next_7_days, 2),
        "recurring_charges_expected_revenue": recurring_expected,
    }
