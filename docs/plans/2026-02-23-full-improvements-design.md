# Full Improvements Design — py-payments-ledger

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement all 11 pending improvements across 5 phases: configurable accounts + multi-currency, Stripe gateway integration, refunds + reconciliation, webhooks + tenant sync + Pact tests, and financial reports + test coverage.

**Architecture:** Extends clean architecture (api/application/infrastructure) with new ports (PaymentGatewayPort), adapters (StripeAdapter, FakeAdapter), models (AccountConfig, Refund, ExchangeRate, WebhookEndpoint, ReconciliationDiscrepancy), and services. All changes maintain double-entry ledger integrity, tenant isolation, idempotency, and outbox pattern.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL 16, Redis 7, RabbitMQ 3, Stripe SDK, Pact Python, Testcontainers.

---

## Phase 1 — Foundation

### Item 11: Configurable Accounts per Tenant

New model `AccountConfig` with fields: id, tenant_id, code, label, account_type (ASSET/LIABILITY/EQUITY/REVENUE/EXPENSE), is_default. Migration seeds CASH + REVENUE as defaults. `post_ledger_for_authorized_payment()` resolves accounts via AccountConfig instead of hardcoded strings.

### Item 3: Multi-Currency

Add `currency` column (String 3, ISO 4217) to `LedgerLine`. New `ExchangeRate` table: from_currency, to_currency, rate, effective_at. Constraint: all lines in a LedgerEntry must share the same currency. Balances endpoint groups by (account, currency).

## Phase 2 — Gateway

### Item 1: Stripe Integration (Port/Adapter)

- `src/application/ports/payment_gateway.py` — Protocol
- `src/infrastructure/gateway/stripe_adapter.py` — StripeAdapter
- `src/infrastructure/gateway/fake.py` — FakeAdapter (tests/dev)
- `PaymentIntent.gateway_ref` — stores Stripe charge/PI ID

Flow: confirm → gateway.authorize() → AUTHORIZED → worker capture → SETTLED.

### Item 9: Intelligent Retry

Exponential backoff with jitter inside StripeAdapter. Circuit breaker: after N consecutive failures, open for X seconds. Transient errors → retry; definitive errors → immediate fail.

## Phase 3 — Financial Operations

### Item 2: Refunds

New model `Refund` (id, tenant_id, payment_intent_id, amount, reason, status, gateway_ref). Endpoint: `POST /v1/payment-intents/{id}/refund`. Reverse ledger: DEBIT REFUND_EXPENSE + CREDIT CASH. PI status → PARTIALLY_REFUNDED or REFUNDED. OutboxEvent: payment.refunded.

### Item 5: Automatic Reconciliation

ReconciliationService with periodic job. Compares Stripe transactions with local PaymentIntents by gateway_ref. Discrepancies stored in `ReconciliationDiscrepancy` table. Alert via outbox event.

## Phase 4 — Integration

### Item 6: Client Webhooks

Models: WebhookEndpoint (tenant_id, url, secret, events[], is_active) + WebhookDelivery (endpoint_id, event_type, payload, status, attempts). HMAC-SHA256 signatures. 3 retries with backoff.

### Item 10: Tenant Sync

Consume tenant.created/updated/deleted from saas.x exchange. Create/update/soft-delete Tenant + seed AccountConfig defaults.

### Item 8: Pact Contract Tests

Provider tests for our API (fluxe-b2b-suite contract). Consumer tests for node-b2b-orders messages.

## Phase 5 — Quality

### Item 4: Financial Reports

Materialized views refreshed every 15min. Endpoints: revenue by period, revenue by tenant, account balances. Filters: from, to, granularity, currency.

### Item 7: Test Coverage ≥80%

Testcontainers for PostgreSQL + Redis + RabbitMQ. Integration tests for all endpoints. Unit tests for all application layer. Fixture factory.
