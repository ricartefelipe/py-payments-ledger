#!/usr/bin/env python3
"""Realistic seed data for py-payments-ledger."""

import os
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text

SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000002"


def _fixed_uuid(seed: str) -> str:
    """Deterministic UUID from seed for idempotent inserts."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"py-payments-ledger.seed.{seed}"))


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S+00")


def seed() -> None:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/payments_ledger",
    )
    engine = create_engine(url)

    with engine.begin() as conn:
        # 0. Ensure Fluxe B2B Suite (global) tenant exists
        conn.execute(
            text("""
                INSERT INTO tenants (id, name, plan, region, created_at)
                VALUES (:id, :name, :plan, :region, :created_at)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": SYSTEM_TENANT_ID,
                "name": "Fluxe B2B Suite",
                "plan": "enterprise",
                "region": "region-a",
                "created_at": _ts(datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)),
            },
        )

        # 1. Account Configs (6 accounts for Fluxe B2B Suite tenant)
        accounts = [
            ("CASH", "Cash", "ASSET", True),
            ("REVENUE", "Revenue", "REVENUE", False),
            ("REFUND_EXPENSE", "Refund Expense", "EXPENSE", False),
            ("CASH_OUT", "Cash Out", "ASSET", False),
            ("DISPUTE_LOSS", "Dispute Loss", "EXPENSE", False),
            ("ACCOUNTS_RECEIVABLE", "Accounts Receivable", "ASSET", False),
        ]
        for code, label, account_type, is_default in accounts:
            conn.execute(
                text("""
                    INSERT INTO account_configs (id, tenant_id, code, label, account_type, is_default, created_at)
                    VALUES (:id, :tenant_id, :code, :label, :account_type, :is_default, :created_at)
                    ON CONFLICT (tenant_id, code) DO NOTHING
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": SYSTEM_TENANT_ID,
                    "code": code,
                    "label": label,
                    "account_type": account_type,
                    "is_default": is_default,
                    "created_at": _ts(datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)),
                },
            )

        # 2. Exchange Rates (3 rates, effective_at: 2026-03-01)
        rates = [
            ("a1000000-0000-0000-0000-000000000001", "USD", "BRL", Decimal("5.05")),
            ("a1000000-0000-0000-0000-000000000002", "EUR", "BRL", Decimal("5.52")),
            ("a1000000-0000-0000-0000-000000000003", "GBP", "BRL", Decimal("6.35")),
        ]
        eff_at = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        for rid, from_c, to_c, rate in rates:
            conn.execute(
                text("""
                    INSERT INTO exchange_rates (id, from_currency, to_currency, rate, effective_at)
                    VALUES (:id, :from_currency, :to_currency, :rate, :effective_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": rid,
                    "from_currency": from_c,
                    "to_currency": to_c,
                    "rate": rate,
                    "effective_at": _ts(eff_at),
                },
            )

        # 3. Gateway Configs (2 configs for Fluxe B2B Suite tenant)
        gateways = [
            (
                "b2000000-0000-0000-0000-000000000001",
                "Stripe",
                True,
                ["BRL", "USD"],
                ["credit_card", "pix"],
            ),
            (
                "b2000000-0000-0000-0000-000000000002",
                "PagSeguro",
                False,
                ["BRL"],
                ["boleto", "pix"],
            ),
        ]
        created = _ts(datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc))
        for gid, provider, is_default, currencies, payment_types in gateways:
            conn.execute(
                text("""
                    INSERT INTO gateway_configs (id, tenant_id, provider, is_default, supported_currencies, payment_types, created_at, updated_at)
                    VALUES (:id, :tenant_id, :provider, :is_default, :supported_currencies, :payment_types, :created_at, :updated_at)
                    ON CONFLICT (tenant_id, provider) DO NOTHING
                """),
                {
                    "id": gid,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "provider": provider,
                    "is_default": is_default,
                    "supported_currencies": currencies,
                    "payment_types": payment_types,
                    "created_at": created,
                    "updated_at": created,
                },
            )

        # 4. Payment Intents (20 intents spanning 3 months)
        # Statuses: SETTLED 12, CREATED 2, AUTHORIZED 2, VOIDED 2, REFUNDED 1, PARTIALLY_REFUNDED 1
        # Amounts R$150 to R$15000, mostly BRL, some USD
        amounts_brl = [
            150,
            450,
            1200,
            2500,
            3800,
            5200,
            6700,
            8400,
            9200,
            11200,
            13500,
            15000,
        ]
        amounts_usd = [50, 120, 350, 800, 1200, 1800, 2500]
        all_amounts_brl = amounts_brl + [2500, 4500, 8900, 10200]  # 16 BRL
        all_amounts_usd = amounts_usd[:4]  # 4 USD
        statuses = [
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "SETTLED",
            "CREATED",
            "CREATED",
            "AUTHORIZED",
            "AUTHORIZED",
            "VOIDED",
            "VOIDED",
            "REFUNDED",
            "PARTIALLY_REFUNDED",
        ]
        base_dt = datetime(2025, 12, 20, 8, 0, 0, tzinfo=timezone.utc)
        pi_ids: list[str] = []

        for i in range(20):
            pi_id = _fixed_uuid(f"payment_intent.{i}")
            pi_ids.append(pi_id)
            if i < 16:
                amount = float(all_amounts_brl[i])
                currency = "BRL"
            else:
                amount = float(all_amounts_usd[i - 16])
                currency = "USD"
            days_offset = (i * 4) % 82  # spread over ~3 months
            created_at = base_dt + timedelta(days=days_offset)
            gateway = "Stripe" if i % 3 != 2 else "PagSeguro"
            conn.execute(
                text("""
                    INSERT INTO payment_intents (id, tenant_id, amount, currency, status, customer_ref, gateway_ref, gateway_provider, created_at, updated_at)
                    VALUES (:id, :tenant_id, :amount, :currency, :status, :customer_ref, :gateway_ref, :gateway_provider, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": pi_id,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "amount": amount,
                    "currency": currency,
                    "status": statuses[i],
                    "customer_ref": f"pedido:ORD-2026-{i+1:03d}",
                    "gateway_ref": f"pi_{gateway.lower()}_{pi_id[:8]}",
                    "gateway_provider": gateway,
                    "created_at": _ts(created_at),
                    "updated_at": _ts(created_at),
                },
            )

        # 5. Ledger Entries + Lines (SETTLED, VOIDED, REFUNDED, PARTIALLY_REFUNDED)
        settled_indices = [i for i, s in enumerate(statuses) if s == "SETTLED"]
        voided_indices = [i for i, s in enumerate(statuses) if s == "VOIDED"]
        refunded_indices = [i for i, s in enumerate(statuses) if s == "REFUNDED"]
        partial_refund_indices = [i for i, s in enumerate(statuses) if s == "PARTIALLY_REFUNDED"]

        for idx in settled_indices:
            pi_id = pi_ids[idx]
            amount = all_amounts_brl[idx] if idx < 16 else all_amounts_usd[idx - 16]
            currency = "BRL" if idx < 16 else "USD"
            base_dt_i = base_dt + timedelta(days=(idx * 4) % 82)
            posted_at = base_dt_i + timedelta(hours=2)
            entry_id = _fixed_uuid(f"ledger_entry.settled.{idx}")
            conn.execute(
                text("""
                    INSERT INTO ledger_entries (id, tenant_id, payment_intent_id, posted_at)
                    VALUES (:id, :tenant_id, :payment_intent_id, :posted_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": entry_id,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "payment_intent_id": pi_id,
                    "posted_at": _ts(posted_at),
                },
            )
            for j, (side, account, amt) in enumerate(
                [("DEBIT", "CASH", amount), ("CREDIT", "REVENUE", amount)]
            ):
                line_id = _fixed_uuid(f"ledger_line.settled.{idx}.{j}")
                conn.execute(
                    text("""
                        INSERT INTO ledger_lines (id, tenant_id, entry_id, side, account, amount, currency)
                        VALUES (:id, :tenant_id, :entry_id, :side, :account, :amount, :currency)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": line_id,
                        "tenant_id": SYSTEM_TENANT_ID,
                        "entry_id": entry_id,
                        "side": side,
                        "account": account,
                        "amount": float(amt),
                        "currency": currency,
                    },
                )

        for idx in voided_indices:
            pi_id = pi_ids[idx]
            amount = all_amounts_brl[idx] if idx < 16 else all_amounts_usd[idx - 16]
            currency = "BRL" if idx < 16 else "USD"
            base_dt_i = base_dt + timedelta(days=(idx * 4) % 82)
            posted_at = base_dt_i + timedelta(hours=4)
            entry_id = _fixed_uuid(f"ledger_entry.voided.{idx}")
            conn.execute(
                text("""
                    INSERT INTO ledger_entries (id, tenant_id, payment_intent_id, posted_at)
                    VALUES (:id, :tenant_id, :payment_intent_id, :posted_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": entry_id,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "payment_intent_id": pi_id,
                    "posted_at": _ts(posted_at),
                },
            )
            for j, (side, account, amt) in enumerate(
                [("DEBIT", "REVENUE", amount), ("CREDIT", "CASH", amount)]
            ):
                line_id = _fixed_uuid(f"ledger_line.voided.{idx}.{j}")
                conn.execute(
                    text("""
                        INSERT INTO ledger_lines (id, tenant_id, entry_id, side, account, amount, currency)
                        VALUES (:id, :tenant_id, :entry_id, :side, :account, :amount, :currency)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": line_id,
                        "tenant_id": SYSTEM_TENANT_ID,
                        "entry_id": entry_id,
                        "side": side,
                        "account": account,
                        "amount": float(amt),
                        "currency": currency,
                    },
                )

        for idx in refunded_indices + partial_refund_indices:
            pi_id = pi_ids[idx]
            amount = all_amounts_brl[idx] if idx < 16 else all_amounts_usd[idx - 16]
            if statuses[idx] == "PARTIALLY_REFUNDED":
                amount = amount * 0.5  # partial refund
            currency = "BRL" if idx < 16 else "USD"
            base_dt_i = base_dt + timedelta(days=(idx * 4) % 82)
            posted_at = base_dt_i + timedelta(hours=6)
            entry_id = _fixed_uuid(f"ledger_entry.refund.{idx}")
            conn.execute(
                text("""
                    INSERT INTO ledger_entries (id, tenant_id, payment_intent_id, posted_at)
                    VALUES (:id, :tenant_id, :payment_intent_id, :posted_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": entry_id,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "payment_intent_id": pi_id,
                    "posted_at": _ts(posted_at),
                },
            )
            for j, (side, account, amt) in enumerate(
                [("DEBIT", "REFUND_EXPENSE", amount), ("CREDIT", "CASH", amount)]
            ):
                line_id = _fixed_uuid(f"ledger_line.refund.{idx}.{j}")
                conn.execute(
                    text("""
                        INSERT INTO ledger_lines (id, tenant_id, entry_id, side, account, amount, currency)
                        VALUES (:id, :tenant_id, :entry_id, :side, :account, :amount, :currency)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": line_id,
                        "tenant_id": SYSTEM_TENANT_ID,
                        "entry_id": entry_id,
                        "side": side,
                        "account": account,
                        "amount": float(amt),
                        "currency": currency,
                    },
                )

        # 6. Refunds (3 - for refunded/voided intents)
        refund_pi_indices = voided_indices[:1] + refunded_indices + partial_refund_indices
        for i, idx in enumerate(refund_pi_indices[:3]):
            pi_id = pi_ids[idx]
            amount = all_amounts_brl[idx] if idx < 16 else all_amounts_usd[idx - 16]
            if statuses[idx] == "PARTIALLY_REFUNDED":
                amount = amount * 0.5
            conn.execute(
                text("""
                    INSERT INTO refunds (id, tenant_id, payment_intent_id, amount, reason, status, gateway_ref, created_at)
                    VALUES (:id, :tenant_id, :payment_intent_id, :amount, :reason, :status, :gateway_ref, :created_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": _fixed_uuid(f"refund.{i}"),
                    "tenant_id": SYSTEM_TENANT_ID,
                    "payment_intent_id": pi_id,
                    "amount": float(amount),
                    "reason": "Solicitação do cliente" if i < 2 else "Erro de cobrança",
                    "status": "COMPLETED",
                    "gateway_ref": f"rf_{pi_id[:8]}",
                    "created_at": _ts(
                        base_dt + timedelta(days=(idx * 4) % 82) + timedelta(hours=5)
                    ),
                },
            )

        # 7. Invoices (10) + Invoice Items
        buyers = [
            "Tech Solutions Ltda",
            "Comércio Digital S.A.",
            "Indústria Nacional Ltda",
            "Agropecuária do Sul",
            "Logística Expresso S.A.",
            "Consultoria Empresarial",
            "Indústria de Alimentos Ltda",
            "Serviços Financeiros S.A.",
            "Distribuidora Centro-Oeste",
            "Comércio Atacadista Ltda",
        ]
        invoice_amounts = [15000, 8400, 2500, 5200, 11200, 3800, 6700, 2500, 9200, 150]
        invoice_statuses = [
            "PAID",
            "PAID",
            "PAID",
            "PAID",
            "PAID",
            "PAID",
            "PAID",
            "ISSUED",
            "ISSUED",
            "DRAFT",
        ]
        for i in range(10):
            inv_id = _fixed_uuid(f"invoice.{i}")
            total_cents = invoice_amounts[i] * 100
            subtotal = int(total_cents * 0.91)
            tax = total_cents - subtotal
            inv_created = base_dt + timedelta(days=i * 7)
            paid_at = (
                _ts(inv_created + timedelta(days=3)) if invoice_statuses[i] == "PAID" else None
            )
            issued_at = (
                _ts(inv_created + timedelta(hours=1)) if invoice_statuses[i] != "DRAFT" else None
            )
            due_at = (
                _ts(inv_created + timedelta(days=10)) if invoice_statuses[i] != "DRAFT" else None
            )
            pi_id = pi_ids[i] if invoice_statuses[i] != "DRAFT" else None
            conn.execute(
                text("""
                    INSERT INTO invoices (id, tenant_id, payment_intent_id, number, status, currency, subtotal_cents, tax_cents, total_cents, issued_at, due_at, paid_at, buyer_name, created_at, updated_at)
                    VALUES (:id, :tenant_id, :payment_intent_id, :number, :status, :currency, :subtotal_cents, :tax_cents, :total_cents, :issued_at, :due_at, :paid_at, :buyer_name, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": inv_id,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "payment_intent_id": pi_id,
                    "number": f"INV-2026-{1000 + i}",
                    "status": invoice_statuses[i],
                    "currency": "BRL",
                    "subtotal_cents": subtotal,
                    "tax_cents": tax,
                    "total_cents": total_cents,
                    "issued_at": issued_at,
                    "due_at": due_at,
                    "paid_at": paid_at,
                    "buyer_name": buyers[i],
                    "created_at": _ts(inv_created),
                    "updated_at": _ts(inv_created),
                },
            )
            # One item per invoice
            unit_cents = subtotal
            conn.execute(
                text("""
                    INSERT INTO invoice_items (id, invoice_id, description, quantity, unit_price_cents, total_cents)
                    VALUES (:id, :invoice_id, :description, :quantity, :unit_price_cents, :total_cents)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": _fixed_uuid(f"invoice_item.{i}"),
                    "invoice_id": inv_id,
                    "description": f"Serviços B2B - Pedido {1000 + i}",
                    "quantity": 1,
                    "unit_price_cents": unit_cents,
                    "total_cents": subtotal,
                },
            )

        # 8. Recurring Charges (3: active, paused, cancelled)
        rc_base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        recurring = [
            (
                "c3000000-0000-0000-0000-000000000001",
                "Plano Pro Mensal",
                29900,
                "monthly",
                "ACTIVE",
                rc_base + timedelta(days=15),
            ),
            (
                "c3000000-0000-0000-0000-000000000002",
                "API Enterprise",
                99900,
                "monthly",
                "PAUSED",
                rc_base + timedelta(days=20),
            ),
            (
                "c3000000-0000-0000-0000-000000000003",
                "Suporte Premium",
                49900,
                "monthly",
                "CANCELLED",
                rc_base + timedelta(days=5),
            ),
        ]
        for rid, desc, amt_cents, interval, status, next_charge in recurring:
            conn.execute(
                text("""
                    INSERT INTO recurring_charges (id, tenant_id, description, amount_cents, currency, interval, next_charge_at, status, created_at, updated_at)
                    VALUES (:id, :tenant_id, :description, :amount_cents, :currency, :interval, :next_charge_at, :status, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": rid,
                    "tenant_id": SYSTEM_TENANT_ID,
                    "description": desc,
                    "amount_cents": amt_cents,
                    "currency": "BRL",
                    "interval": interval,
                    "next_charge_at": _ts(next_charge),
                    "status": status,
                    "created_at": _ts(rc_base),
                    "updated_at": _ts(rc_base),
                },
            )

        # 9. Outbox Events (10 past events, PUBLISHED)
        event_types = [
            "payment.settled",
            "payment.settled",
            "payment.voided",
            "refund.completed",
            "payment.settled",
            "invoice.issued",
            "payment.settled",
            "refund.completed",
            "invoice.issued",
            "payment.voided",
        ]
        for i in range(10):
            pi_idx = min(i, len(pi_ids) - 1)
            amt = all_amounts_brl[pi_idx] if pi_idx < 16 else all_amounts_usd[pi_idx - 16]
            curr = "BRL" if pi_idx < 16 else "USD"
            payload_json = (
                f'{{"payment_intent_id":"{pi_ids[pi_idx]}","amount":{amt},"currency":"{curr}"}}'
            )
            ts = _ts(base_dt + timedelta(days=i * 6))
            conn.execute(
                text("""
                    INSERT INTO outbox_events (id, tenant_id, aggregate_type, aggregate_id, event_type, payload, status, attempts, available_at, created_at)
                    VALUES (:id, :tenant_id, :aggregate_type, :aggregate_id, :event_type, CAST(:payload AS jsonb), :status, :attempts, :available_at, :created_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": _fixed_uuid(f"outbox.{i}"),
                    "tenant_id": SYSTEM_TENANT_ID,
                    "event_type": event_types[i],
                    "aggregate_type": "PaymentIntent",
                    "aggregate_id": pi_ids[pi_idx],
                    "payload": payload_json,
                    "status": "PUBLISHED",
                    "attempts": 1,
                    "available_at": ts,
                    "created_at": ts,
                },
            )

        # 10. Audit Log (15 entries)
        audit_entries = [
            ("CREATE", "payment-intent"),
            ("UPDATE", "payment-intent"),
            ("CREATE", "refund"),
            ("CREATE", "invoice"),
            ("CREATE", "payment-intent"),
            ("UPDATE", "payment-intent"),
            ("UPDATE", "payment-intent"),
            ("CREATE", "refund"),
            ("CREATE", "invoice"),
            ("CREATE", "payment-intent"),
            ("UPDATE", "gateway-config"),
            ("UPDATE", "payment-intent"),
            ("CREATE", "refund"),
            ("CREATE", "invoice"),
            ("CREATE", "payment-intent"),
        ]
        for i, (action, target) in enumerate(audit_entries):
            pi_idx = min(i % 10, len(pi_ids) - 1)
            conn.execute(
                text("""
                    INSERT INTO audit_log (id, tenant_id, actor_sub, action, target, detail, correlation_id, created_at)
                    VALUES (:id, :tenant_id, :actor_sub, :action, :target, CAST(:detail AS jsonb), :correlation_id, :created_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": _fixed_uuid(f"audit.{i}"),
                    "tenant_id": SYSTEM_TENANT_ID,
                    "actor_sub": "ops@demo.example.com",
                    "action": action,
                    "target": target,
                    "detail": f'{{"payment_intent_id":"{pi_ids[pi_idx]}","amount":1000,"currency":"BRL","ref":"ORD-{1000+i}"}}',
                    "correlation_id": str(uuid.uuid4()),
                    "created_at": _ts(base_dt + timedelta(days=i * 5)),
                },
            )

        # 11. Demo Corp tenant — payment intents alinhados aos pedidos do orders seed
        conn.execute(
            text("""
                INSERT INTO tenants (id, name, plan, region, created_at)
                VALUES (:id, :name, :plan, :region, :created_at)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": DEMO_TENANT_ID,
                "name": "Demo Corp",
                "plan": "pro",
                "region": "sa-east-1",
                "created_at": _ts(datetime(2025, 12, 15, 0, 0, 0, tzinfo=timezone.utc)),
            },
        )
        demo_intents: list[tuple[str, float, str, str]] = [
            ("a0000003-0000-4000-8000-000000000003", 1899.0, "SETTLED", "Pedido cust-sp-001"),
            ("a0000004-0000-4000-8000-000000000004", 3698.6, "SETTLED", "Pedido cust-sul-001"),
            ("a0000006-0000-4000-8000-000000000006", 4599.0, "SETTLED", "Pedido cust-metal-001"),
            ("a0000007-0000-4000-8000-000000000007", 11396.0, "AUTHORIZED", "Pedido cust-off-001"),
            ("a0000009-0000-4000-8000-000000000009", 28148.0, "SETTLED", "Pedido cust-tec-001"),
            ("a0000010-0000-4000-8000-000000000010", 1379.7, "SETTLED", "Pedido cust-ferr-001"),
            ("a0000011-0000-4000-8000-000000000011", 6248.5, "SETTLED", "Pedido cust-const-001"),
            ("a0000013-0000-4000-8000-000000000013", 6097.0, "SETTLED", "Pedido cust-hosp-001"),
            ("a0000014-0000-4000-8000-000000000014", 6844.0, "SETTLED", "Pedido cust-arm-001"),
            ("a0000001-0000-4000-8000-000000000001", 9799.8, "CREATED", "Pedido cust-abc-001"),
            ("a0000002-0000-4000-8000-000000000002", 4648.5, "CREATED", "Pedido cust-xyz-001"),
            ("a0000015-0000-4000-8000-000000000015", 3198.0, "VOIDED", "Pedido cancelado"),
        ]
        for i, (order_id, amount, status, label) in enumerate(demo_intents):
            pi_id = _fixed_uuid(f"demo.payment_intent.{order_id}")
            created_at = base_dt + timedelta(days=10 + i * 3)
            conn.execute(
                text("""
                    INSERT INTO payment_intents (id, tenant_id, amount, currency, status, customer_ref, gateway_ref, gateway_provider, created_at, updated_at)
                    VALUES (:id, :tenant_id, :amount, :currency, :status, :customer_ref, :gateway_ref, :gateway_provider, :created_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": pi_id,
                    "tenant_id": DEMO_TENANT_ID,
                    "amount": amount,
                    "currency": "BRL",
                    "status": status,
                    "customer_ref": f"order:{order_id}",
                    "gateway_ref": f"fake_demo_{pi_id[:8]}",
                    "gateway_provider": "fake",
                    "created_at": _ts(created_at),
                    "updated_at": _ts(created_at),
                },
            )
            if status == "SETTLED":
                entry_id = _fixed_uuid(f"demo.ledger_entry.{order_id}")
                posted_at = created_at + timedelta(hours=1)
                conn.execute(
                    text("""
                        INSERT INTO ledger_entries (id, tenant_id, payment_intent_id, posted_at)
                        VALUES (:id, :tenant_id, :payment_intent_id, :posted_at)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": entry_id,
                        "tenant_id": DEMO_TENANT_ID,
                        "payment_intent_id": pi_id,
                        "posted_at": _ts(posted_at),
                    },
                )
                for j, (side, account, amt) in enumerate(
                    [("DEBIT", "CASH", amount), ("CREDIT", "REVENUE", amount)]
                ):
                    conn.execute(
                        text("""
                            INSERT INTO ledger_lines (id, tenant_id, entry_id, side, account, amount, currency)
                            VALUES (:id, :tenant_id, :entry_id, :side, :account, :amount, :currency)
                            ON CONFLICT (id) DO NOTHING
                        """),
                        {
                            "id": _fixed_uuid(f"demo.ledger_line.{order_id}.{j}"),
                            "tenant_id": DEMO_TENANT_ID,
                            "entry_id": entry_id,
                            "side": side,
                            "account": account,
                            "amount": float(amt),
                            "currency": "BRL",
                        },
                    )

    print("Seed completed!")


if __name__ == "__main__":
    seed()
