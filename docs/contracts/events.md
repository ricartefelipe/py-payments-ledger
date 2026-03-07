# RabbitMQ Event Contracts

Technical specification of event contracts between the B2B platform backend services. Events are published via transactional outbox patterns and consumed via RabbitMQ exchanges and queues.

## Services overview

| Service | Role | Exchange (publishes) | Outbox |
|---------|------|----------------------|--------|
| spring-saas-core | Control plane, tenants, policies, flags | `saas.events` | JpaOutboxPublisher + OutboxPublisher |
| node-b2b-orders | Order lifecycle, inventory | `orders.x` | OutboxPublisher |
| py-payments-ledger | Payments, reconciliation | `payments.x` | OutboxPublisher |

---

## spring-saas-core

**Exchange:** `saas.events`  
**Routing key pattern:** `saas.{aggregateType}.{eventType}`

### Published events

| Event | Routing Key | Payload Schema | Description |
|-------|-------------|----------------|-------------|
| `tenant.created` | `saas.tenant.created` | `{ name, plan, region }` | New tenant registered |
| `tenant.updated` | `saas.tenant.updated` | `{ name, plan }` | Tenant plan or metadata changed |
| `tenant.deleted` | `saas.tenant.deleted` | `{ name, plan }` | Tenant removed |
| `policy.created` | `saas.policy.created` | `{ permissionCode, effect }` | ABAC/RBAC policy added |
| `policy.updated` | `saas.policy.updated` | `{ permissionCode, effect }` | Policy effect changed |
| `policy.deleted` | `saas.policy.deleted` | `{ permissionCode }` | Policy removed |
| `flag.created` | `saas.flag.created` | Feature flag payload | Feature flag created |
| `flag.updated` | `saas.flag.updated` | Feature flag payload | Feature flag updated |
| `flag.deleted` | `saas.flag.deleted` | Feature flag payload | Feature flag removed |

### Consumes

spring-saas-core does not consume events from other services (it is the source of truth for tenants, policies, and flags).

---

## node-b2b-orders

**Exchange:** `orders.x`  
**Routing key pattern:** Service-defined per event

### Published events

| Event | Routing Key | Payload Schema | Description |
|-------|-------------|----------------|-------------|
| `order.created` | `order.created` | Order payload | Order created |
| `order.reserved` | `order.reserved` | Order + inventory | Inventory reserved by worker |
| `order.confirmed` | `order.confirmed` | Order payload | Order confirmed |
| `order.cancelled` | `order.cancelled` | Order payload | Order cancelled |
| `order.paid` | `order.paid` | Order + payment ref | Payment settled; order marked paid |
| `payment.charge_requested` | — | Charge payload | Published to **payments** exchange when order is confirmed |

**Note:** `payment.charge_requested` is routed to the **payments** exchange (consumed by py-payments-ledger), not the orders exchange.

### Consumes

| Source | Event(s) | Queue | Description |
|-------|----------|-------|-------------|
| py-payments-ledger | `payment.settled` | `orders.payments` | Updates order when payment is settled; publishes `order.paid` |

---

## py-payments-ledger

**Exchange:** `payments.x`  
**Routing key pattern:** Service-defined per event

### Published events

| Event | Routing Key | Payload Schema | Description |
|-------|-------------|----------------|-------------|
| `payment.intent.created` | `payment.intent.created` | Intent payload | Payment intent created |
| `payment.authorized` | `payment.authorized` | Auth payload | Payment authorized/confirmed |
| `payment.settled` | `payment.settled` | `{ order_id, tenant_id, ... }` | Ledger entry posted; consumed by orders |
| `payment.refunded` | `payment.refunded` | Refund payload | Refund processed |
| `reconciliation.discrepancy_found` | `reconciliation.discrepancy_found` | Discrepancy payload | Reconciliation found issues |

### Consumes

| Source | Event(s) | Queue | Condition | Description |
|--------|----------|-------|------------|-------------|
| node-b2b-orders | `payment.charge_requested`, `order.confirmed` | — | `ORDERS_INTEGRATION_ENABLED=true` | Creates payment intent when order is confirmed |
| spring-saas-core | `tenant.created`, `tenant.updated`, `tenant.deleted` | — | `SAAS_INTEGRATION_ENABLED=true` | Syncs tenant metadata for ledger |

---

## Event flow (text diagram)

```
┌─────────────────────┐
│ spring-saas-core   │
│ (saas.events)      │
└─────────┬───────────┘
          │ tenant.created / tenant.updated / tenant.deleted
          │ policy.* / flag.* (broadcast)
          ▼
┌─────────────────────┐     payment.charge_requested      ┌─────────────────────┐
│ node-b2b-orders    │ ─────────────────────────────────► │ py-payments-ledger  │
│ (orders.x)         │                                    │ (payments.x)        │
└─────────▲──────────┘                                    └─────────┬───────────┘
          │                                                         │
          │ payment.settled                                          │ tenant.*
          └─────────────────────────────────────────────────────────┘
```

**Flow summary:**

1. **spring-saas-core → py-payments-ledger:** Tenant lifecycle events (when `SAAS_INTEGRATION_ENABLED=true`).
2. **node-b2b-orders → py-payments-ledger:** `payment.charge_requested` when order is confirmed (when `ORDERS_INTEGRATION_ENABLED=true`).
3. **py-payments-ledger → node-b2b-orders:** `payment.settled` when payment is posted; orders consume via `orders.payments` queue and publish `order.paid`.

---

## Configuration

| Variable | Service | Default | Description |
|----------|---------|---------|-------------|
| `SAAS_INTEGRATION_ENABLED` | py-payments-ledger | — | If `true`, consumes tenant events from spring-saas-core |
| `ORDERS_INTEGRATION_ENABLED` | py-payments-ledger | — | If `true`, consumes `payment.charge_requested` / `order.confirmed` from node-b2b-orders |

When integration flags are `false`, services operate in standalone mode without cross-service event consumption.

---

## Common payload fields

Events may include standard metadata:

| Field | Type | Description |
|-------|------|-------------|
| `eventId` | string (UUID) | Unique event identifier |
| `occurredAt` | string (ISO 8601) | Timestamp when event occurred |
| `aggregateId` | string | ID of the affected aggregate |
| `aggregateType` | string | Aggregate type (e.g. `tenant`, `order`, `payment`) |
| `tenantId` | string | Tenant context (when applicable) |
| `correlationId` | string | Distributed tracing ID |

Individual event schemas may extend these fields.
