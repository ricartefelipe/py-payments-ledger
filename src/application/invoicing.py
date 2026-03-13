from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.application.security import _audit
from src.infrastructure.db.models import Invoice, InvoiceItem
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InvoiceItemDTO(BaseModel):
    id: str
    description: str
    quantity: int
    unit_price_cents: int
    total_cents: int


class InvoiceDTO(BaseModel):
    id: str
    tenant_id: str
    payment_intent_id: str | None = None
    number: str
    status: str
    currency: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    issued_at: str | None = None
    due_at: str | None = None
    paid_at: str | None = None
    buyer_name: str | None = None
    buyer_email: str | None = None
    buyer_tax_id: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    items: list[InvoiceItemDTO] = []


def _to_dto(inv: Invoice) -> InvoiceDTO:
    return InvoiceDTO(
        id=str(inv.id),
        tenant_id=inv.tenant_id,
        payment_intent_id=str(inv.payment_intent_id) if inv.payment_intent_id else None,
        number=inv.number,
        status=inv.status,
        currency=inv.currency,
        subtotal_cents=inv.subtotal_cents,
        tax_cents=inv.tax_cents,
        total_cents=inv.total_cents,
        issued_at=inv.issued_at.isoformat() if inv.issued_at else None,
        due_at=inv.due_at.isoformat() if inv.due_at else None,
        paid_at=inv.paid_at.isoformat() if inv.paid_at else None,
        buyer_name=inv.buyer_name,
        buyer_email=inv.buyer_email,
        buyer_tax_id=inv.buyer_tax_id,
        notes=inv.notes,
        created_at=inv.created_at.isoformat(),
        updated_at=inv.updated_at.isoformat(),
        items=[
            InvoiceItemDTO(
                id=str(it.id),
                description=it.description,
                quantity=it.quantity,
                unit_price_cents=it.unit_price_cents,
                total_cents=it.total_cents,
            )
            for it in inv.items
        ],
    )


def _generate_invoice_number(session: Session, tenant_id: str) -> str:
    last = (
        session.execute(
            select(func.count()).select_from(Invoice).where(Invoice.tenant_id == tenant_id)
        ).scalar()
        or 0
    )
    seq = last + 1
    return f"INV-{tenant_id[:8].upper()}-{seq:06d}"


def create_invoice(
    session: Session,
    tenant_id: str,
    currency: str,
    items: list[dict],
    buyer_info: dict | None = None,
    payment_intent_id: str | None = None,
    notes: str | None = None,
    due_at: datetime | None = None,
) -> InvoiceDTO:
    if not items:
        raise http_problem(
            400, "Bad Request", "at least one item is required", instance="/v1/invoices"
        )
    if currency not in ("BRL", "USD", "EUR"):
        raise http_problem(400, "Bad Request", "unsupported currency", instance="/v1/invoices")

    buyer = buyer_info or {}
    now = _utcnow()

    with safe_begin(session):
        number = _generate_invoice_number(session, tenant_id)

        inv_items: list[InvoiceItem] = []
        subtotal = 0
        for it in items:
            qty = it.get("quantity", 1)
            unit = it["unit_price_cents"]
            line_total = qty * unit
            subtotal += line_total
            inv_items.append(
                InvoiceItem(
                    description=it["description"],
                    quantity=qty,
                    unit_price_cents=unit,
                    total_cents=line_total,
                )
            )

        tax_cents = buyer.get("tax_cents", 0)
        total_cents = subtotal + tax_cents

        pi_id = uuid.UUID(payment_intent_id) if payment_intent_id else None

        inv = Invoice(
            tenant_id=tenant_id,
            payment_intent_id=pi_id,
            number=number,
            status="DRAFT",
            currency=currency,
            subtotal_cents=subtotal,
            tax_cents=tax_cents,
            total_cents=total_cents,
            due_at=due_at,
            buyer_name=buyer.get("name"),
            buyer_email=buyer.get("email"),
            buyer_tax_id=buyer.get("tax_id"),
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        inv.items = inv_items
        session.add(inv)
        session.flush()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "invoice.created",
        f"invoice:{inv.id}",
        {"number": number, "total_cents": total_cents, "currency": currency},
    )

    return _to_dto(inv)


def issue_invoice(session: Session, invoice_id: str, tenant_id: str) -> InvoiceDTO:
    with safe_begin(session):
        inv = session.execute(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id, Invoice.id == uuid.UUID(invoice_id))
            .with_for_update()
        ).scalar_one_or_none()
        if not inv:
            raise http_problem(
                404,
                "Not Found",
                "invoice not found",
                instance=f"/v1/invoices/{invoice_id}/issue",
            )
        if inv.status != "DRAFT":
            raise http_problem(
                409,
                "Conflict",
                f"cannot issue invoice in status {inv.status}",
                instance=f"/v1/invoices/{invoice_id}/issue",
            )
        inv.status = "ISSUED"
        inv.issued_at = _utcnow()
        inv.updated_at = _utcnow()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "invoice.issued",
        f"invoice:{inv.id}",
        {"number": inv.number},
    )

    return _to_dto(inv)


def mark_invoice_paid(
    session: Session,
    invoice_id: str,
    tenant_id: str,
    payment_intent_id: str | None = None,
) -> InvoiceDTO:
    with safe_begin(session):
        inv = session.execute(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id, Invoice.id == uuid.UUID(invoice_id))
            .with_for_update()
        ).scalar_one_or_none()
        if not inv:
            raise http_problem(
                404,
                "Not Found",
                "invoice not found",
                instance=f"/v1/invoices/{invoice_id}/pay",
            )
        if inv.status not in ("DRAFT", "ISSUED"):
            raise http_problem(
                409,
                "Conflict",
                f"cannot mark as paid from status {inv.status}",
                instance=f"/v1/invoices/{invoice_id}/pay",
            )
        inv.status = "PAID"
        inv.paid_at = _utcnow()
        inv.updated_at = _utcnow()
        if payment_intent_id:
            inv.payment_intent_id = uuid.UUID(payment_intent_id)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "invoice.paid",
        f"invoice:{inv.id}",
        {"number": inv.number, "payment_intent_id": payment_intent_id or ""},
    )

    return _to_dto(inv)


def cancel_invoice(session: Session, invoice_id: str, tenant_id: str) -> InvoiceDTO:
    with safe_begin(session):
        inv = session.execute(
            select(Invoice)
            .where(Invoice.tenant_id == tenant_id, Invoice.id == uuid.UUID(invoice_id))
            .with_for_update()
        ).scalar_one_or_none()
        if not inv:
            raise http_problem(
                404,
                "Not Found",
                "invoice not found",
                instance=f"/v1/invoices/{invoice_id}/cancel",
            )
        if inv.status == "PAID":
            raise http_problem(
                409,
                "Conflict",
                "cannot cancel a paid invoice",
                instance=f"/v1/invoices/{invoice_id}/cancel",
            )
        if inv.status == "CANCELLED":
            return _to_dto(inv)
        inv.status = "CANCELLED"
        inv.updated_at = _utcnow()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "invoice.cancelled",
        f"invoice:{inv.id}",
        {"number": inv.number},
    )

    return _to_dto(inv)


def list_invoices(
    session: Session,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[InvoiceDTO], int]:
    q = select(Invoice).where(Invoice.tenant_id == tenant_id)
    if status:
        q = q.where(Invoice.status == status)

    count_q = select(func.count()).select_from(q.subquery())
    total = session.execute(count_q).scalar() or 0

    q = q.order_by(Invoice.created_at.desc()).offset(offset).limit(limit)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows], total


def get_invoice(session: Session, invoice_id: str, tenant_id: str) -> InvoiceDTO:
    inv = session.execute(
        select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.id == uuid.UUID(invoice_id))
    ).scalar_one_or_none()
    if not inv:
        raise http_problem(
            404,
            "Not Found",
            "invoice not found",
            instance=f"/v1/invoices/{invoice_id}",
        )
    return _to_dto(inv)
