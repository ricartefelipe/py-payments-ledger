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
**Routing key pattern:** `saas.{AGGREGATE_TYPE}.{eventType}` — `AGGREGATE_TYPE` is the outbox field in **uppercase** (`TENANT`, `POLICY`, `FLAG`, …). The RabbitMQ key is built in `OutboxPublisher` as `routingKeyPrefix + "." + aggregateType + "." + eventType` (see `application.yml`: `routing-key-prefix: saas`).

### Published events

| Event (`eventType`) | Routing Key | Payload Schema | Description |
|-------|-------------|----------------|-------------|
| `tenant.created` | `saas.TENANT.tenant.created` | `{ name, plan, region }` | New tenant registered |
| `tenant.updated` | `saas.TENANT.tenant.updated` | `{ name, plan }` | Tenant plan or metadata changed |
| `tenant.deleted` | `saas.TENANT.tenant.deleted` | `{ name, plan }` | Tenant removed |
| `policy.created` | `saas.POLICY.policy.created` | `{ permissionCode, effect }` | ABAC/RBAC policy added |
| `policy.updated` | `saas.POLICY.policy.updated` | `{ permissionCode, effect }` | Policy effect changed |
| `policy.deleted` | `saas.POLICY.policy.deleted` | `{ permissionCode }` | Policy removed |
| `flag.created` | `saas.FLAG.flag.created` | Feature flag payload | Feature flag created |
| `flag.toggled` | `saas.FLAG.flag.toggled` | Feature flag payload | Feature flag updated (enabled/rollout/roles); there is no `flag.updated` event |
| `flag.deleted` | `saas.FLAG.flag.deleted` | Feature flag payload | Feature flag removed |

#### Additional published events (same exchange and routing pattern)

These are emitted by the outbox alongside the table above:

| Event (`eventType`) | Routing Key | Payload (summary) | Source |
|-------|-------------|-------------------|--------|
| `user.registered` | `saas.USER.user.registered` | `email`, `name`, `tenantId` | `UserUseCase` |
| `user.updated` | `saas.USER.user.updated` | User fields | `UserManagementUseCase` |
| `user.deleted` | `saas.USER.user.deleted` | User id / tenant | `UserManagementUseCase` |
| `user.invited` | `saas.USER.user.invited` | Invite payload | `UserManagementUseCase` |
| `user.password_reset_requested` | `saas.USER.user.password_reset_requested` | `userId`, `tenantId`, `tokenId`, `rawToken` | `UserUseCase` |
| `subscription.trial_started` | `saas.SUBSCRIPTION.subscription.trial_started` | `tenantId`, `planSlug`, `trialEndsAt` | `SubscriptionUseCase` |
| `subscription.activated` | `saas.SUBSCRIPTION.subscription.activated` | `tenantId`, `planSlug` (optional `reactivated`) | `SubscriptionUseCase` |
| `subscription.upgraded` | `saas.SUBSCRIPTION.subscription.upgraded` | `tenantId`, `previousPlan`, `newPlan` | `SubscriptionUseCase` |
| `subscription.downgraded` | `saas.SUBSCRIPTION.subscription.downgraded` | `tenantId`, `previousPlan`, `newPlan` | `SubscriptionUseCase` |
| `subscription.cancelled` | `saas.SUBSCRIPTION.subscription.cancelled` | `tenantId`, `planSlug`, `cancelledAt` or `reason` | `SubscriptionUseCase` |
| `subscription.expired` | `saas.SUBSCRIPTION.subscription.expired` | `tenantId`, `planSlug` | `SubscriptionUseCase` (expired trials) |
| `onboarding.completed` | `saas.ONBOARDING.onboarding.completed` | `tenantId`, `tenantName`, `plan`, `adminEmail` | `OnboardingUseCase` |

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
| `order.updated` | `order.updated` | Order payload | Broadcast on several lifecycle updates (service layer) |
| `stock.reserved` | `stock.reserved` | Order + inventory | Worker publishes after reserving inventory (schema: `inventory.reserved.json`) |
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
| `payment.voided` | `payment.voided` | Void payload | Payment voided |
| `reconciliation.discrepancy_found` | `reconciliation.discrepancy_found` | Discrepancy payload | Reconciliation found issues |

**Further outbox event types** (not all have standalone JSON schemas yet) include: `payment.retry_exhausted`, `payment.splits.processed`, `payout.created`, `payout.completed`, `payout.failed`, `dispute.opened`, `dispute.accepted`, `dispute.resolved`. Search `event_type=` in `py-payments-ledger` for the authoritative list.

### Consumes

| Source | Event(s) | Queue | Condition | Description |
|--------|----------|-------|------------|-------------|
| node-b2b-orders | `payment.charge_requested`, `order.confirmed` | — | `ORDERS_INTEGRATION_ENABLED=true` | Creates payment intent when order is confirmed |
| spring-saas-core | `tenant.created`, `tenant.updated`, `tenant.deleted` (RabbitMQ routing keys: `saas.TENANT.tenant.*`) | — | `SAAS_INTEGRATION_ENABLED=true` | Syncs tenant metadata for ledger; consumer unwraps the Core envelope (`payload` + `aggregateId`) |

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
| `SAAS_EXCHANGE` | py-payments-ledger | `saas.events` | Must match **spring-saas-core** `spring.rabbitmq.exchange` (default `saas.events`). If misconfigured, tenant events are never delivered. |
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

---

## Schema Validation

Event contracts are formally defined as **JSON Schema (draft-07)** files in `docs/contracts/schemas/`:

| Schema file | Events covered | `$id` |
|-------------|---------------|-------|
| [`tenant-event.schema.json`](schemas/tenant-event.schema.json) | `tenant.created`, `tenant.updated`; enum also allows `tenant.suspended`, `tenant.reactivated` (not yet published by Core) | `https://fluxe.io/schemas/events/tenant-event/v1` |
| [`policy-event.schema.json`](schemas/policy-event.schema.json) | `policy.created`, `policy.updated`, `policy.deleted` | `https://fluxe.io/schemas/events/policy-event/v1` |
| [`flag-event.schema.json`](schemas/flag-event.schema.json) | `flag.created`, `flag.deleted`; enum still lists `flag.updated` but runtime emits **`flag.toggled`** for updates | `https://fluxe.io/schemas/events/flag-event/v1` |

### Versioning strategy

Schemas follow **URL-based versioning** through the `$id` field:

- The version segment in the `$id` URL (e.g. `/v1`) identifies the schema version.
- The `version` field inside each event payload (e.g. `"1.0"`) carries the contract version at runtime.
- **Backward-compatible changes** (adding optional fields) keep the same major version.
- **Breaking changes** (removing fields, changing types, tightening enums) bump to a new major version (e.g. `/v2`), producing a new schema file while the previous version remains available.
- Consumers should validate against the schema matching the `version` field in the event.

### How to validate

Any JSON Schema draft-07 validator can be used. Example with `ajv-cli`:

```bash
npx ajv-cli validate -s docs/contracts/schemas/tenant-event.schema.json -d event.json
```

Or programmatically in Java tests using `everit-org/json-schema` or `networknt/json-schema-validator`.
